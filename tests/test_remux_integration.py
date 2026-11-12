"""End-to-end checks using real FFmpeg/ffprobe processes and temporary media."""

import asyncio
import logging
import os
import shutil
import unittest
from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from media_fixtures import MediaFixtures
from pydantic import ValidationError

from mushin.artifacts import ArtifactOutput, LocalArtifactStore
from mushin.exceptions import MediaToolError
from mushin.media import MediaProbe, MediaTools, ProcessRunner
from mushin.media.container import matches_container
from mushin.models.run import NodeExecutionContext
from mushin.nodes import RemuxVideo


class SlowRunner(ProcessRunner):
    """Use real FFmpeg, looping at input rate until cancellation or timeout."""

    output = None

    async def ffmpeg(self, tools, arguments, **kwargs):
        self.output = Path(arguments[-1])
        return await super().ffmpeg(tools, ["-re", "-stream_loop", "-1", *arguments], **kwargs)


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg and ffprobe required")
class RemuxIntegrationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.tools = MediaTools.discover()

    def setUp(self):
        self.workspace = TemporaryDirectory(prefix="mushin-integration-")
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        self.store_root = self.root / "artifacts"
        self.store = LocalArtifactStore(self.store_root)
        self.fixtures = MediaFixtures(self.root, self.tools)
        self.probe = MediaProbe(self.tools)
        self.context = NodeExecutionContext(run_id=uuid4(), node_id=uuid4(), logger=logging.getLogger("test.integration"))

    async def remux(self, ref, container="mkv", *, streams=None, tools=None, runner=None, timeout=10):
        config = {"container": container, "timeout": timeout}
        if streams is not None:
            config["streams"] = streams
        result = await RemuxVideo(self.store, tools or self.tools, runner=runner).execute({"file": ref}, config, self.context)
        return result["file"]

    async def assert_isolated(self, ref, source, original, *, published):
        self.assertEqual(source.read_bytes(), original)
        self.assertEqual(await self.store.read(ref), original)
        self.assertEqual(len(list(self.store_root.iterdir())), published)
        self.assertFalse(any(path.name.startswith(".") for path in self.store_root.iterdir()))

    async def test_mp4_remux_preserves_video_and_all_audio_tracks_and_is_idempotent(self):
        self.fixtures.require(muxers=["mp4"])
        source = await self.fixtures.video("multi.mkv", audio_tracks=2)
        original = source.read_bytes()
        ref = await self.store.import_file(source)
        before = await self.probe.probe_artifact(self.store, ref)
        converted = await self.remux(ref, "mp4")
        self.assertNotEqual(converted.content_id, ref.content_id)
        self.assertEqual(converted.filename, "multi.mp4")
        info = await self.probe.probe_artifact(self.store, converted)
        self.assertEqual([stream.codec_name for stream in info.streams], ["mpeg4", "aac", "aac"])
        self.assertEqual([stream.tags.get("language") for stream in info.streams[1:]], ["eng", "fra"])
        # Compare probed durations: AAC encoder delay is already in the source.
        self.assertAlmostEqual(info.duration, before.duration, delta=0.05)
        self.assertEqual(await self.remux(converted, "mp4"), converted)
        await self.assert_isolated(ref, source, original, published=2)

    async def test_webm_conversion_and_reverse_mkv_conversion_are_distinct_and_idempotent(self):
        self.fixtures.require(muxers=["webm"])
        source = await self.fixtures.video("vp8.mkv", video_codec="libvpx", audio_codec="libopus")
        original = source.read_bytes()
        ref = await self.store.import_file(source)
        webm = await self.remux(ref, "webm")
        self.assertNotEqual(webm.content_id, ref.content_id)
        info = await self.probe.probe_artifact(self.store, webm)
        self.assertEqual([stream.codec_name for stream in info.streams], ["vp8", "opus"])
        async with self.store.input_path(webm) as path:
            self.assertTrue(matches_container(path, info, "webm"))
            self.assertFalse(matches_container(path, info, "matroska"))
        self.assertEqual(await self.remux(webm, "webm"), webm)
        mkv = await self.remux(webm, "mkv")
        self.assertNotEqual(mkv.content_id, webm.content_id)
        self.assertEqual(await self.remux(mkv, "mkv"), mkv)
        await self.assert_isolated(ref, source, original, published=3)

    async def test_subtitles_attachment_chapters_preserved_while_selecting_first_audio(self):
        source = await self.fixtures.video("rich.mkv", audio_tracks=2, extras=True)
        original = source.read_bytes()
        ref = await self.store.import_file(source)
        before = await self.probe.probe_artifact(self.store, ref)
        self.assertEqual([s.codec_type for s in before.streams], ["video", "audio", "audio", "subtitle", "attachment"])
        converted = await self.remux(ref, streams={"audio": "first"})
        after = await self.probe.probe_artifact(self.store, converted)
        self.assertEqual([s.codec_type for s in after.streams], ["video", "audio", "subtitle", "attachment"])
        self.assertEqual(after.streams[1].tags["language"], "eng")
        self.assertEqual(after.streams[2].codec_name, "subrip")
        self.assertEqual(after.streams[3].tags["filename"], "notes.txt")
        self.assertEqual(after.streams[3].tags["mimetype"], "text/plain")
        self.assertEqual(len(after.chapters), 1)
        self.assertEqual(after.chapters[0].tags["title"], "Opening")
        self.assertAlmostEqual(after.chapters[0].end_time, before.chapters[0].end_time, delta=0.05)
        # Inspect actual embedded bytes rather than just attachment metadata.
        attachment = self.root / "extracted.txt"
        async with self.store.input_path(converted) as path:
            await ProcessRunner().ffmpeg(self.tools, ["-v", "error", "-dump_attachment:t:0", str(attachment),
                                                      "-i", str(path), "-map", "0:v:0", "-c", "copy", "-f", "null", "-"], timeout=10)
        self.assertEqual(attachment.read_bytes(), (self.root / "notes.txt").read_bytes())
        self.assertEqual(await self.remux(converted, streams={"audio": "first"}), converted)
        await self.assert_isolated(ref, source, original, published=2)

    async def test_explicit_drops_and_chapters_are_idempotent(self):
        source = await self.fixtures.video("rich.mkv", audio_tracks=2, extras=True)
        original = source.read_bytes()
        ref = await self.store.import_file(source)
        policy = {"audio": "first", "subtitles": "none", "attachments": "none", "chapters": "drop"}
        converted = await self.remux(ref, streams=policy)
        info = await self.probe.probe_artifact(self.store, converted)
        self.assertEqual([s.codec_type for s in info.streams], ["video", "audio"])
        self.assertEqual(info.chapters, ())
        self.assertEqual(await self.remux(converted, streams=policy), converted)
        await self.assert_isolated(ref, source, original, published=2)

    async def test_cover_art_preserved_and_explicitly_dropped(self):
        source = await self.fixtures.with_cover()
        original = source.read_bytes()
        ref = await self.store.import_file(source)
        before = await self.probe.probe_artifact(self.store, ref)
        self.assertEqual(sum(s.is_attached_picture for s in before.streams), 1)
        self.assertEqual(await self.remux(ref, "mp4"), ref)
        kept = await self.remux(ref, "mp4", streams={"audio": "none"})
        info = await self.probe.probe_artifact(self.store, kept)
        self.assertEqual(sum(s.is_attached_picture for s in info.streams), 1)
        self.assertEqual([s.codec_name for s in info.streams], ["mpeg4", "png"])
        dropped = await self.remux(ref, "mp4", streams={"pictures": "none"})
        info = await self.probe.probe_artifact(self.store, dropped)
        self.assertFalse(any(s.is_attached_picture for s in info.streams))
        self.assertEqual([s.codec_name for s in info.streams], ["mpeg4", "aac"])
        await self.assert_isolated(ref, source, original, published=3)

    async def test_malformed_input_and_unsupported_configuration_publish_nothing(self):
        source = self.root / "broken.mkv"
        source.write_bytes(b"Not a video, despite the filename")
        original = source.read_bytes()
        ref = await self.store.import_file(source)
        with self.assertRaises(MediaToolError) as caught:
            await self.remux(ref)
        self.assertEqual(caught.exception.code, "command_failed")
        with self.assertRaises(ValidationError):
            await self.remux(ref, "avi")
        await self.assert_isolated(ref, source, original, published=1)

    async def test_incompatible_webm_preserves_source_and_publishes_nothing(self):
        self.fixtures.require(muxers=["webm"])
        source = await self.fixtures.video("mpeg4.mkv")
        original = source.read_bytes()
        ref = await self.store.import_file(source)
        with self.assertRaises(MediaToolError) as caught:
            await self.remux(ref, "webm")
        self.assertEqual(caught.exception.code, "remux_failed")
        self.assertTrue(caught.exception.stderr)
        await self.assert_isolated(ref, source, original, published=1)

    async def test_missing_executables_publish_nothing(self):
        self.fixtures.require(muxers=["mp4"])
        source = await self.fixtures.video("valid.mkv")
        original = source.read_bytes()
        ref = await self.store.import_file(source)
        for name in ("ffmpeg", "ffprobe"):
            with self.subTest(tool=name):
                executable = replace(getattr(self.tools, name), path=self.root / f"missing-{name}")
                tools = replace(self.tools, **{name: executable})
                with self.assertRaises(MediaToolError) as caught:
                    await self.remux(ref, "mp4", tools=tools)
                self.assertEqual(caught.exception.code, "launch_failed")
                await self.assert_isolated(ref, source, original, published=1)

    @unittest.skipUnless(os.name == "posix", "POSIX child liveness checks")
    async def test_timeout_and_both_cancellation_methods_reap_ffmpeg_before_cleanup(self):
        self.fixtures.require(muxers=["mp4"])
        source = await self.fixtures.video("valid.mkv")
        original = source.read_bytes()
        ref = await self.store.import_file(source)
        launch = asyncio.create_subprocess_exec
        for mode in ("timeout", "event", "task"):
            with self.subTest(mode=mode):
                self.context.cancellation.clear()
                children = []
                runner = SlowRunner(grace_period=0.2)

                async def capture(executable, *args, **kwargs):
                    child = await launch(executable, *args, **kwargs)
                    if str(executable) == str(self.tools.ffmpeg.path):
                        children.append(child)
                    return child

                output_file = self.store.output_file

                @asynccontextmanager
                async def checked_output(**kwargs) -> AsyncGenerator[ArtifactOutput]:
                    async with output_file(**kwargs) as output:
                        try:
                            yield output
                        finally:
                            # This runs before the artifact context removes
                            # staging, proving the child was reaped first.
                            self.assertTrue(output.path.exists())
                            self.assertEqual(len(children), 1)
                            self.assertIsNotNone(children[0].returncode)
                            with self.assertRaises(ProcessLookupError):
                                os.kill(children[0].pid, 0)

                with (patch("mushin.media.process.asyncio.create_subprocess_exec", side_effect=capture),
                      patch.object(self.store, "output_file", checked_output)):
                    task = asyncio.create_task(self.remux(ref, "mp4", runner=runner, timeout=2 if mode == "timeout" else 10))
                    try:
                        # Synchronize on real FFmpeg output; a fixed sleep might
                        # interrupt the input probe instead on a slower host.
                        async with asyncio.timeout(5):
                            while not children or runner.output is None or not runner.output.exists():
                                if task.done():
                                    await task
                                    self.fail("FFmpeg finished before interruption")
                                await asyncio.sleep(0.01)
                        self.assertTrue(any(p.name.startswith(".input-") for p in self.store_root.iterdir()))
                        if mode == "event":
                            self.context.cancellation.set()
                        elif mode == "task":
                            task.cancel()
                        with self.assertRaises(MediaToolError if mode == "timeout" else asyncio.CancelledError) as caught:
                            await task
                        if mode == "timeout":
                            self.assertEqual(caught.exception.code, "process_timeout")
                    finally:
                        if not task.done():
                            task.cancel()
                        await asyncio.gather(task, return_exceptions=True)
                self.assertEqual(len(children), 1)
                self.assertIsNotNone(children[0].returncode)
                with self.assertRaises(ProcessLookupError):
                    os.kill(children[0].pid, 0)
                self.assertFalse(runner.output.exists())
                await self.assert_isolated(ref, source, original, published=1)
