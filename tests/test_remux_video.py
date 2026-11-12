import asyncio
import json
import logging
import shutil
import unittest
from dataclasses import replace
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock
from uuid import uuid4

from mushin.artifacts import LocalArtifactStore
from mushin.exceptions import MediaToolError
from mushin.media import MediaInfo, MediaProbe, MediaTools, ProcessResult, ProcessRunner, StreamSelectionPolicy
from mushin.media.tools import ToolExecutable
from mushin.media.container import matches_container
from mushin.models.node import Node, Workflow, WorkflowInput, WorkflowOutput
from mushin.models.registry import NodeRegistry
from mushin.models.run import NodeExecutionContext, WorkflowRunner, WorkflowRunStatus
from mushin.nodes import RemuxVideo


def metadata(codec="h264", *, container="matroska,webm", audio=True):
    streams = [{"index": 0, "codec_type": "video", "codec_name": codec, "width": 16, "height": 16}]
    if audio:
        streams.append({"index": 2, "codec_type": "audio", "codec_name": "aac", "channels": 1})
    return {"format": {"format_name": container, "duration": "1.0"}, "streams": streams}


def ebml_header(doctype):
    element = b"\x42\x82" + bytes([0x80 | len(doctype)]) + doctype
    return b"\x1a\x45\xdf\xa3" + bytes([0x80 | len(element)]) + element


class RemuxVideoTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.workspace = TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        self.store = LocalArtifactStore(self.root)
        executable = ToolExecutable(Path("/tools/media"), "test")
        self.tools = MediaTools(executable, executable, frozenset({"matroska", "mp4", "webm"}),
                                frozenset(), frozenset(), frozenset())
        self.runner = AsyncMock(spec=ProcessRunner)
        self.runner.ffprobe.side_effect = [ProcessResult(json.dumps(metadata()).encode(), "", 0),
                                          ProcessResult(json.dumps(metadata()).encode(), "", 0)]

        async def write_output(tools, arguments, **kwargs):
            Path(arguments[-1]).write_bytes(b"remuxed")
            return ProcessResult(b"", "", 0)

        self.runner.ffmpeg.side_effect = write_output
        self.node = RemuxVideo(self.store, self.tools, runner=self.runner)
        self.context = NodeExecutionContext(run_id=uuid4(), node_id=uuid4(), logger=logging.getLogger("test"))

    async def execute(self, config=None):
        self.source = await self.store.create(b"source", filename="Example.original.mp4")
        return await self.node.execute({"file": self.source}, config or {"container": "matroska"}, self.context)

    async def assert_source_only(self):
        self.assertEqual(await self.store.read(self.source), b"source")
        self.assertEqual(len(list(self.root.iterdir())), 1)

    async def test_new_artifact_and_explicit_copy_arguments(self):
        result = await self.execute()
        ref = result["file"]
        self.assertEqual(ref.filename, "Example.original.mkv")
        self.assertNotEqual(ref.content_id, self.source.content_id)
        self.assertEqual(await self.store.read(ref), b"remuxed")
        self.assertEqual(await self.store.read(self.source), b"source")
        arguments = self.runner.ffmpeg.call_args.args[1]
        self.assertIn(["-c", "copy"], [arguments[i:i+2] for i in range(len(arguments)-1)])
        self.assertIn(["-f", "matroska"], [arguments[i:i+2] for i in range(len(arguments)-1)])
        self.assertIn(["-map", "0:2"], [arguments[i:i+2] for i in range(len(arguments)-1)])
        self.assertEqual(Path(arguments[-1]).suffix, "")
        self.assertFalse(Path(arguments[-1]).exists())
        self.assertIs(self.runner.ffmpeg.call_args.kwargs["cancellation"], self.context.cancellation)

    async def test_explicit_stream_drop_verified_against_selection(self):
        self.runner.ffprobe.side_effect = [ProcessResult(json.dumps(metadata()).encode(), "", 0),
                                          ProcessResult(json.dumps(metadata(audio=False)).encode(), "", 0)]
        await self.execute({"container": "matroska", "streams": {"audio": "none"}})
        self.assertNotIn("0:2", self.runner.ffmpeg.call_args.args[1])

    async def test_mkv_alias_uses_matroska_muxer_and_extension(self):
        result = await self.execute({"container": "mkv"})
        self.assertEqual(result["file"].filename, "Example.original.mkv")
        arguments = self.runner.ffmpeg.call_args.args[1]
        self.assertEqual(arguments[arguments.index("-f") + 1], "matroska")

    async def test_correct_container_returns_same_reference_repeatedly(self):
        # A misleading logical extension must not defeat content identification.
        self.source = await self.store.create(ebml_header(b"matroska"), filename="misnamed.mp4")
        self.runner.ffprobe.side_effect = None
        self.runner.ffprobe.return_value = ProcessResult(json.dumps(metadata()).encode(), "", 0)
        # A no-op needs no output muxer capability.
        self.node = RemuxVideo(self.store, replace(self.tools, muxers=frozenset()), runner=self.runner)
        for container in ("mkv", "matroska", "mkv"):
            result = await self.node.execute({"file": self.source}, {"container": container}, self.context)
            self.assertIs(result["file"], self.source)
        self.runner.ffmpeg.assert_not_called()
        self.assertEqual(self.runner.ffprobe.call_count, 3)
        self.assertEqual(len(list(self.root.iterdir())), 1)

    async def test_same_container_with_requested_stream_change_remuxes(self):
        self.source = await self.store.create(ebml_header(b"matroska"), filename="video.mkv")
        self.runner.ffprobe.side_effect = [ProcessResult(json.dumps(metadata()).encode(), "", 0),
                                          ProcessResult(json.dumps(metadata(audio=False)).encode(), "", 0)]
        result = await self.node.execute({"file": self.source},
                                         {"container": "mkv", "streams": {"audio": "none"}}, self.context)
        self.assertNotEqual(result["file"].content_id, self.source.content_id)
        self.runner.ffmpeg.assert_awaited_once()

    async def test_same_container_with_requested_chapter_drop_remuxes(self):
        self.source = await self.store.create(ebml_header(b"matroska"), filename="video.mkv")
        before = metadata()
        before["chapters"] = [{"id": 1, "start_time": "0", "end_time": "1"}]
        self.runner.ffprobe.side_effect = [ProcessResult(json.dumps(before).encode(), "", 0),
                                          ProcessResult(json.dumps(metadata()).encode(), "", 0)]
        result = await self.node.execute({"file": self.source},
                                         {"container": "mkv", "streams": {"chapters": "drop"}}, self.context)
        self.assertNotEqual(result["file"].content_id, self.source.content_id)
        self.runner.ffmpeg.assert_awaited_once()

    async def test_noop_honors_cancellation_after_probe(self):
        self.source = await self.store.create(ebml_header(b"matroska"), filename="video.mkv")

        async def cancel(tools, arguments, **kwargs):
            self.context.cancellation.set()
            return ProcessResult(json.dumps(metadata()).encode(), "", 0)

        self.runner.ffprobe.side_effect = cancel
        with self.assertRaises(asyncio.CancelledError):
            await self.node.execute({"file": self.source}, {"container": "mkv"}, self.context)
        self.runner.ffmpeg.assert_not_called()
        self.assertEqual(len(list(self.root.iterdir())), 1)

    async def test_mismatched_output_is_discarded(self):
        for after in (metadata(codec="hevc"), metadata(audio=False), metadata(container="avi")):
            with self.subTest(after=after):
                before_files = set(self.root.iterdir())
                self.runner.ffprobe.side_effect = [ProcessResult(json.dumps(metadata()).encode(), "", 0),
                                                  ProcessResult(json.dumps(after).encode(), "", 0)]
                with self.assertRaises(MediaToolError) as caught:
                    await self.execute()
                self.assertEqual(caught.exception.code, "remux_verification_failed")
                self.assertEqual(set(self.root.iterdir()) - before_files, {self.root / self.source.content_id.hex})

    async def test_process_failure_and_cancellation_discard_output(self):
        for error in (MediaToolError("command_failed", "failure", returncode=1, stderr="codec unsupported"),
                      MediaToolError("process_timeout", "timeout"), asyncio.CancelledError()):
            with self.subTest(error=type(error).__name__):
                self.runner.ffprobe.side_effect = [ProcessResult(json.dumps(metadata()).encode(), "", 0)]

                async def fail(tools, arguments, **kwargs):
                    Path(arguments[-1]).write_bytes(b"partial")
                    raise error

                self.runner.ffmpeg.side_effect = fail
                with self.assertRaises(type(error)) as caught:
                    await self.execute()
                if isinstance(error, MediaToolError):
                    self.assertEqual(caught.exception.code, "remux_failed" if error.code == "command_failed" else error.code)
                self.assertEqual(await self.store.read(self.source), b"source")
                self.assertFalse(any(path.name.startswith(".") for path in self.root.iterdir()))

    async def test_cancellation_before_publication_discards_output(self):
        async def cancel_after_probe(tools, arguments, **kwargs):
            if self.runner.ffprobe.call_count == 2:
                self.context.cancellation.set()
            return ProcessResult(json.dumps(metadata()).encode(), "", 0)

        self.runner.ffprobe.side_effect = cancel_after_probe
        with self.assertRaises(asyncio.CancelledError):
            await self.execute()
        await self.assert_source_only()

    async def test_invalid_config_is_rejected_before_processing(self):
        registry = NodeRegistry()
        registry.register(RemuxVideo.spec, self.node)
        for config in ({"container": "avi"}, {"container": "mp4", "timeout": 0},
                       {"container": "mp4", "streams": {"audio": "invalid"}}):
            node = Node(type=RemuxVideo.spec.type, config=config)
            workflow = Workflow(name="Invalid remux", nodes=[node])
            run = await WorkflowRunner(registry).run(workflow)
            self.assertEqual(run.status, WorkflowRunStatus.FAILED)
            self.assertEqual(run.error.code, "invalid_workflow")
        self.runner.ffmpeg.assert_not_called()
        self.runner.ffprobe.assert_not_called()

    async def test_missing_muxer_fails_before_remuxing(self):
        self.node = RemuxVideo(self.store, replace(self.tools, muxers=frozenset()), runner=self.runner)
        with self.assertRaises(MediaToolError) as caught:
            await self.execute()
        self.assertEqual(caught.exception.code, "missing_capability")
        self.runner.ffprobe.assert_awaited_once()
        self.runner.ffmpeg.assert_not_called()
        await self.assert_source_only()

    def test_chapter_loss_fails_verification(self):
        before = metadata()
        before["chapters"] = [{"id": 1, "start_time": "0", "end_time": "1"}]
        info = MediaInfo.model_validate(before)
        with self.assertRaisesRegex(MediaToolError, "chapters"):
            RemuxVideo._verify(info, MediaInfo.model_validate(metadata()), StreamSelectionPolicy().select(info), "matroska")

    def test_opaque_attachments_allow_unknown_codec_but_require_metadata(self):
        raw = metadata()
        raw["streams"].append({"index": 3, "codec_type": "attachment",
                               "tags": {"filename": "notes.txt", "mimetype": "text/plain"}})
        before = MediaInfo.model_validate(raw)
        selection = StreamSelectionPolicy().select(before)
        RemuxVideo._verify(before, before, selection, "matroska")
        raw["streams"][-1]["tags"]["filename"] = "different.txt"
        with self.assertRaisesRegex(MediaToolError, "changed filename"):
            RemuxVideo._verify(before, MediaInfo.model_validate(raw), selection, "matroska")
        raw = metadata(codec=None)
        before = MediaInfo.model_validate(raw)
        with self.assertRaisesRegex(MediaToolError, "type and codec"):
            RemuxVideo._verify(before, before, StreamSelectionPolicy().select(before), "matroska")


@unittest.skipUnless(shutil.which("ffmpeg") and shutil.which("ffprobe"), "FFmpeg and ffprobe required")
class RealRemuxTests(unittest.IsolatedAsyncioTestCase):
    async def test_valid_remux_and_incompatible_container(self):
        tools = MediaTools.discover()
        if "ffv1" not in tools.encoders or not {"avi", "matroska", "webm"} <= tools.muxers:
            self.skipTest("Fixture needs FFV1 encoding and AVI/Matroska/WebM muxers")
        with TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / "clip.avi"
            runner = ProcessRunner()
            await runner.ffmpeg(tools, ["-v", "error", "-f", "lavfi", "-i", "color=size=16x16:rate=5",
                                         "-t", "0.4", "-c:v", "ffv1", str(source)], timeout=10)
            original = source.read_bytes()
            store = LocalArtifactStore(root / "artifacts")
            ref = await store.import_file(source)
            registry = NodeRegistry()
            registry.register(RemuxVideo.spec, RemuxVideo(store, tools))
            node = Node(type=RemuxVideo.spec.type, config={"container": "mkv"})
            workflow = Workflow(name="Remux", nodes=[node],
                                inputs=[WorkflowInput(name="file", target_node=node.id, target_port="file")],
                                outputs=[WorkflowOutput(name="file", source_node=node.id, source_port="file")])
            run = await WorkflowRunner(registry).run(workflow, inputs={"file": ref})
            self.assertEqual(run.status, WorkflowRunStatus.SUCCEEDED, run.error)
            converted = run.outputs["file"]
            self.assertNotEqual(converted.content_id, ref.content_id)
            info = await MediaProbe(tools).probe_artifact(store, converted)
            self.assertEqual(info.streams[0].codec_name, "ffv1")
            self.assertAlmostEqual(info.duration, 0.4, places=2)
            repeated = await WorkflowRunner(registry).run(workflow, inputs={"file": converted})
            self.assertEqual(repeated.status, WorkflowRunStatus.SUCCEEDED, repeated.error)
            self.assertEqual(repeated.outputs["file"], converted)
            workflow.nodes[0].config = {"container": "webm"}
            # Matroska and WebM share probe aliases, but must not be treated
            # as the same container for the idempotent path.
            failed = await WorkflowRunner(registry).run(workflow, inputs={"file": converted})
            self.assertEqual(failed.status, WorkflowRunStatus.FAILED)
            self.assertIn("Cannot copy selected streams into webm", failed.error.message)
            self.assertEqual(failed.outputs, {})
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(await store.read(ref), original)
            self.assertEqual(len(list((root / "artifacts").iterdir())), 2)


class ContainerIdentityTests(unittest.TestCase):
    def test_matroska_and_webm_are_distinct_despite_shared_probe_aliases(self):
        info = MediaInfo.model_validate(metadata())
        with TemporaryDirectory() as directory:
            path = Path(directory) / "misleading.mkv"
            for container in ("matroska", "webm"):
                path.write_bytes(ebml_header(container.encode()))
                self.assertTrue(matches_container(path, info, container))
                other = "webm" if container == "matroska" else "matroska"
                self.assertFalse(matches_container(path, info, other))
            for header in (b"matroska", b"\x1a\x45\xdf\xa3\xff", ebml_header(b"matroska")[:-1],
                           b"\x1a\x45\xdf\xa3\x80matroska", b"\x1a\x45\xdf\xa3\x83\x00\x00\x00"):
                path.write_bytes(header)
                self.assertFalse(matches_container(path, info, "matroska"))

    def test_mp4_brands_exclude_quicktime_and_other_shared_aliases(self):
        for brand, matches in (("isom", True), ("mp42", True), ("qt  ", False),
                               ("3gp4", False), ("mjp2", False), ("unknown", False), ("", False)):
            info = metadata(container="mov,mp4,m4a,3gp,3g2,mj2")
            info["format"]["tags"] = {"major_brand": brand}
            self.assertEqual(matches_container(Path("unused"), MediaInfo.model_validate(info), "mp4"), matches)
