from __future__ import annotations

import asyncio
import math
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from mushin.exceptions import MediaToolError
from mushin.media.tools import MediaTools


@dataclass(frozen=True)
class ProcessResult:
    """Complete stdout and a bounded stderr tail from a successful process."""

    stdout: bytes
    stderr: str
    returncode: int


class ProcessRunner:
    """Run a direct child with bounded capture and cleanup before returning.

    Arguments never pass through a shell. Children must not spawn descendants
    that inherit output pipes. Cancellation waits for the direct child to be
    reaped and pipes drained, even during launch or repeated cancellation.
    """

    def __init__(self, *, stdout_limit: int = 4 * 1024 * 1024, stderr_limit: int = 16 * 1024, grace_period: float = 1.0) -> None:
        for name, value in (("stdout_limit", stdout_limit), ("stderr_limit", stderr_limit)):
            if not isinstance(value, int) or value < 1:
                raise ValueError(f"{name} must be a positive integer")
        if not math.isfinite(grace_period) or grace_period <= 0:
            raise ValueError("grace_period must be finite and positive")
        self.stdout_limit = stdout_limit
        self.stderr_limit = stderr_limit
        self.grace_period = grace_period

    async def run(
        self, executable: str | Path, arguments: Sequence[str], *, timeout: float | None = None, cancellation: asyncio.Event | None = None
    ) -> ProcessResult:
        """Timeout includes launch and processing; cleanup may take extra time."""
        if timeout is not None and (not math.isfinite(timeout) or timeout <= 0):
            raise ValueError("timeout must be finite and positive")
        if isinstance(arguments, (str, bytes)):
            raise TypeError("arguments must be a sequence of individual strings")
        if cancellation is not None and cancellation.is_set():
            raise asyncio.CancelledError
        stop = asyncio.Event()
        # Cancelling during spawn can lose the child handle. Signal the
        # supervisor instead so it owns the child through launch and cleanup.
        operation = asyncio.create_task(self._execute(str(executable), tuple(arguments), stop))
        cancelled = asyncio.create_task(cancellation.wait()) if cancellation is not None else None
        try:
            watched = {operation} if cancelled is None else {operation, cancelled}
            # wait() leaves pending tasks alive so the supervisor can clean up.
            done, _ = await asyncio.wait(watched, timeout=timeout, return_when=asyncio.FIRST_COMPLETED)
            # A cancellation request wins even if completion is ready too.
            if cancelled is not None and cancelled in done:
                raise asyncio.CancelledError
            if operation not in done:
                stop.set()
                result = await asyncio.shield(operation)
                raise MediaToolError(
                    "process_timeout", f"{executable} exceeded {timeout}s", executable=str(executable), returncode=result.returncode, stderr=result.stderr
                )
            return operation.result()
        finally:
            stop.set()
            if cancelled is not None:
                cancelled.cancel()
            interrupted = False
            # Repeated task.cancel() calls must not interrupt child cleanup.
            while not operation.done():
                try:
                    await asyncio.shield(operation)
                except asyncio.CancelledError:
                    interrupted = True
                except Exception:  # noqa: BLE001 - Don't catch blind exception
                    break
            if not operation.cancelled():
                # Cancellation may have won the race; still retrieve task errors.
                operation.exception()
            if cancelled is not None:
                # Event.wait owns no external resources.
                cancelled.add_done_callback(lambda task: None if task.cancelled() else task.exception())
            if interrupted:
                raise asyncio.CancelledError

    async def ffmpeg(
        self, tools: MediaTools, arguments: Sequence[str], *, timeout: float | None = None, cancellation: asyncio.Event | None = None
    ) -> ProcessResult:
        """Run FFmpeg with interactive stdin explicitly disabled."""
        return await self.run(tools.ffmpeg.path, ["-nostdin", *arguments], timeout=timeout, cancellation=cancellation)

    async def ffprobe(
        self, tools: MediaTools, arguments: Sequence[str], *, timeout: float | None = None, cancellation: asyncio.Event | None = None
    ) -> ProcessResult:
        return await self.run(tools.ffprobe.path, arguments, timeout=timeout, cancellation=cancellation)

    async def _execute(self, executable: str, arguments: tuple[str, ...], stop: asyncio.Event) -> ProcessResult:
        try:
            process = await asyncio.create_subprocess_exec(
                executable,
                *arguments,
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except OSError as exc:
            raise MediaToolError("launch_failed", f"Cannot launch {executable}: {exc}", executable=executable) from exc
        stdout = bytearray()
        stderr = bytearray()
        overflow = False

        async def drain(stream: asyncio.StreamReader, *, is_stderr: bool) -> None:
            nonlocal overflow
            while chunk := await stream.read(64 * 1024):
                if is_stderr:
                    # Failure details usually appear last; older log output can go.
                    stderr.extend(chunk)
                    del stderr[: -self.stderr_limit]
                else:
                    available = self.stdout_limit - len(stdout)
                    stdout.extend(chunk[:available])
                    if len(chunk) > available:
                        overflow = True
                        stop.set()
                        # Keep draining after overflow so shutdown cannot block
                        # on a full pipe while the child is being terminated.

        assert process.stdout is not None and process.stderr is not None
        readers = [asyncio.create_task(drain(process.stdout, is_stderr=False)), asyncio.create_task(drain(process.stderr, is_stderr=True))]
        # A child can block on either pipe; waiting for exit alone can deadlock.
        completion = asyncio.gather(process.wait(), *readers)
        stopped = asyncio.create_task(stop.wait())
        try:
            await asyncio.wait({completion, stopped}, return_when=asyncio.FIRST_COMPLETED)
        finally:
            if process.returncode is None:
                try:
                    process.terminate()
                except ProcessLookupError:
                    pass
                try:
                    # The grace deadline must not cancel pipe-draining tasks.
                    await asyncio.wait_for(asyncio.shield(completion), self.grace_period)
                except TimeoutError:
                    if process.returncode is None:
                        try:
                            process.kill()
                        except ProcessLookupError:
                            pass
            await completion
            stopped.cancel()
            await asyncio.gather(stopped, return_exceptions=True)
        assert process.returncode is not None
        result = ProcessResult(bytes(stdout), stderr.decode("utf-8", errors="replace"), process.returncode)
        if overflow:
            raise MediaToolError(
                "output_limit",
                f"{executable} exceeded stdout limit of {self.stdout_limit} bytes",
                executable=executable,
                returncode=result.returncode,
                stderr=result.stderr,
            )
        # Deliberate termination is reported by run() as timeout/cancellation,
        # rather than being mistaken for an ordinary command failure.
        if result.returncode and not stop.is_set():
            raise MediaToolError(
                "command_failed",
                f"{executable} exited with {result.returncode}: {result.stderr}",
                executable=executable,
                returncode=result.returncode,
                stderr=result.stderr,
            )
        return result
