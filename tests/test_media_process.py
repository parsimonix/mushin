import asyncio
import os
import signal
import sys
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from mushin.artifacts import LocalArtifactStore
from mushin.exceptions import MediaToolError
from mushin.media import ProcessRunner
from mushin.media.tools import MediaTools, ToolExecutable


class ProcessRunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.runner = ProcessRunner(grace_period=0.1)
        self.workspace = TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)

    async def python(self, script, **kwargs):
        return await self.runner.run(sys.executable, ["-c", script], timeout=5, **kwargs)

    async def ready(self, path):
        async with asyncio.timeout(5):
            while not path.exists():
                await asyncio.sleep(0.01)
        return int(path.read_text())

    def assert_reaped(self, pid):
        with self.assertRaises(ProcessLookupError):
            os.kill(pid, 0)

    def sleeper(self, marker, *, ignore_term=False):
        return (
            "import os, signal, time; from pathlib import Path; "
            + ("signal.signal(signal.SIGTERM, signal.SIG_IGN); " if ignore_term else "")
            + f"Path({str(marker)!r}).write_text(str(os.getpid())); time.sleep(60)"
        )

    async def test_arguments_stdin_and_concurrent_pipe_drain(self):
        result = await self.python(
            "import os, sys; assert sys.stdin.read() == ''; "
            "os.write(1, b'x' * 200000); os.write(2, b'y' * 200000 + b'END')"
        )
        self.assertEqual(result.stdout, b"x" * 200000)
        self.assertEqual(len(result.stderr), self.runner.stderr_limit)
        self.assertTrue(result.stderr.endswith("END"))
        literal = "spaces ; $(echo unsafe)"
        result = await self.runner.run(sys.executable, ["-c", "import sys; print(sys.argv[1])", literal], timeout=5)
        self.assertEqual(result.stdout.decode().strip(), literal)

    async def test_nonzero_exit_and_launch_error(self):
        with self.assertRaises(MediaToolError) as caught:
            await self.python("import sys; sys.stderr.write('diagnostic'); sys.exit(7)")
        self.assertEqual(caught.exception.code, "command_failed")
        self.assertEqual(caught.exception.returncode, 7)
        self.assertEqual(caught.exception.stderr, "diagnostic")
        with self.assertRaises(MediaToolError) as caught:
            await self.runner.run(self.root / "missing", [])
        self.assertEqual(caught.exception.code, "launch_failed")

    async def test_stdout_overflow_stops_child(self):
        marker = self.root / "pid"
        script = ("import os, time; from pathlib import Path; "
                  f"Path({str(marker)!r}).write_text(str(os.getpid())); "
                  "os.write(1, b'x' * 100000); time.sleep(60)")
        runner = ProcessRunner(stdout_limit=100, grace_period=0.1)
        with self.assertRaises(MediaToolError) as caught:
            await runner.run(sys.executable, ["-c", script], timeout=5)
        self.assertEqual(caught.exception.code, "output_limit")
        self.assert_reaped(int(marker.read_text()))

    @unittest.skipUnless(os.name == "posix", "POSIX signal escalation")
    async def test_timeout_kills_uncooperative_child(self):
        marker = self.root / "pid"
        # Start the deadline after the child has installed its signal handler.
        task = asyncio.create_task(self.runner.run(
            sys.executable, ["-c", self.sleeper(marker, ignore_term=True)], timeout=1))
        pid = await self.ready(marker)
        with self.assertRaises(MediaToolError) as caught:
            await task
        self.assertEqual(caught.exception.code, "process_timeout")
        self.assertEqual(caught.exception.returncode, -signal.SIGKILL)
        self.assert_reaped(pid)

    @unittest.skipUnless(os.name == "posix", "POSIX signal termination")
    async def test_timeout_terminates_and_cleans_partial_output(self):
        marker = self.root / "pid"
        store = LocalArtifactStore(self.root / "artifacts")
        with self.assertRaises(MediaToolError) as caught:
            async with store.output_file(filename="output.mkv") as output:
                output.path.write_bytes(b"partial")
                await self.runner.run(sys.executable, ["-c", self.sleeper(marker)], timeout=1)
        self.assertEqual(caught.exception.code, "process_timeout")
        self.assertEqual(caught.exception.returncode, -signal.SIGTERM)
        self.assert_reaped(int(marker.read_text()))
        self.assertIsNone(output.ref)
        self.assertEqual(list((self.root / "artifacts").iterdir()), [])

    async def test_event_and_task_cancellation_reap_before_artifact_cleanup(self):
        for mode in ("event", "task"):
            with self.subTest(mode=mode):
                marker = self.root / f"pid-{mode}"
                store = LocalArtifactStore(self.root / mode)
                event = asyncio.Event()
                output = None

                async def work():
                    nonlocal output
                    async with store.output_file(filename="output.mkv") as output:
                        output.path.write_bytes(b"partial")
                        await self.python(self.sleeper(marker), cancellation=event)

                task = asyncio.create_task(work())
                pid = await self.ready(marker)
                event.set() if mode == "event" else task.cancel()
                with self.assertRaises(asyncio.CancelledError):
                    await task
                self.assert_reaped(pid)
                self.assertIsNone(output.ref)
                self.assertFalse(output.path.exists())
                self.assertEqual(list((self.root / mode).iterdir()), [])

    @unittest.skipUnless(os.name == "posix", "POSIX signal escalation")
    async def test_repeated_cancellation_still_reaps(self):
        marker = self.root / "pid"
        task = asyncio.create_task(self.python(self.sleeper(marker, ignore_term=True)))
        pid = await self.ready(marker)
        task.cancel()
        await asyncio.sleep(0.02)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assert_reaped(pid)

    async def test_cancellation_during_launch_retains_child_ownership(self):
        entered = asyncio.Event()
        release = asyncio.Event()
        launch = asyncio.create_subprocess_exec
        children = []

        async def delayed(*args, **kwargs):
            entered.set()
            await release.wait()
            child = await launch(*args, **kwargs)
            children.append(child)
            return child

        with patch("mushin.media.process.asyncio.create_subprocess_exec", side_effect=delayed):
            task = asyncio.create_task(self.python("import time; time.sleep(60)"))
            await entered.wait()
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(len(children), 1)
        self.assert_reaped(children[0].pid)

    async def test_precancelled_request_does_not_launch(self):
        event = asyncio.Event()
        event.set()
        with patch("mushin.media.process.asyncio.create_subprocess_exec") as launch:
            with self.assertRaises(asyncio.CancelledError):
                await self.python("pass", cancellation=event)
        launch.assert_not_called()

    async def test_tool_wrappers_disable_ffmpeg_stdin(self):
        tool = ToolExecutable(Path("/tools/ffmpeg"), "test")
        probe = ToolExecutable(Path("/tools/ffprobe"), "test")
        tools = MediaTools(tool, probe, frozenset(), frozenset(), frozenset(), frozenset())
        with patch.object(self.runner, "run", new_callable=AsyncMock) as run:
            await self.runner.ffmpeg(tools, ["-version"], timeout=2)
            run.assert_awaited_once_with(tool.path, ["-nostdin", "-version"], timeout=2, cancellation=None)
            run.reset_mock()
            await self.runner.ffprobe(tools, ["-version"])
            run.assert_awaited_once_with(probe.path, ["-version"], timeout=None, cancellation=None)

    async def test_invalid_limits_and_timeout(self):
        for kwargs in ({"stdout_limit": 0}, {"stderr_limit": -1}, {"grace_period": float("inf")}):
            with self.assertRaises(ValueError):
                ProcessRunner(**kwargs)
        with self.assertRaises(ValueError):
            await self.runner.run(sys.executable, [], timeout=0)
        with self.assertRaises(TypeError):
            await self.runner.run(sys.executable, "-version")
