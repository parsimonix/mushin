import asyncio
import json
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock

from pydantic import ValidationError

from mushin.artifacts import LocalArtifactStore
from mushin.exceptions import MediaToolError
from mushin.media import MediaInfo, MediaProbe, ProcessResult, StreamSelectionPolicy
from mushin.media.tools import MediaTools, ToolExecutable


def document():
    return {
        "format": {"format_name": "matroska,webm", "duration": "12.500", "tags": {"title": "Example"}},
        "streams": [
            {"index": 0, "codec_type": "video", "codec_name": "h264", "width": 640, "height": 360},
            {"index": 2, "codec_type": "audio", "codec_name": "aac", "tags": {"language": "eng"}},
            {"index": 3, "codec_type": "audio", "codec_name": "aac", "tags": {"language": "fra"}},
            {"index": 4, "codec_type": "subtitle", "codec_name": "subrip"},
            {"index": 5, "codec_type": "attachment", "codec_name": "ttf"},
            {"index": 7, "codec_type": "video", "codec_name": "mjpeg", "disposition": {"attached_pic": 1}},
            {"index": 8, "codec_type": "data", "codec_name": "bin_data"},
        ],
        "chapters": [{"id": 1, "start_time": "0.000", "end_time": "12.500", "tags": {"title": "Opening"}}],
    }


class ProbeMetadataTests(unittest.TestCase):
    def test_typed_metadata_and_unknown_extra_fields(self):
        raw = document()
        raw["streams"][0]["future_property"] = {"anything": True}
        info = MediaInfo.model_validate_json(json.dumps(raw))
        self.assertEqual(info.duration, 12.5)
        self.assertEqual(info.format.names, ("matroska", "webm"))
        self.assertEqual(info.streams[0].width, 640)
        self.assertEqual(info.streams[1].tags["language"], "eng")
        self.assertTrue(info.streams[5].is_attached_picture)
        self.assertEqual(info.chapters[0].end_time, 12.5)

    def test_unknown_duration_is_not_inferred(self):
        for value in (None, "N/A"):
            raw = document()
            raw["format"]["duration"] = value
            raw["streams"][0]["duration"] = "5.0"
            self.assertIsNone(MediaInfo.model_validate(raw).duration)
        raw["format"].pop("duration")
        self.assertIsNone(MediaInfo.model_validate(raw).duration)

    def test_invalid_structure_and_values(self):
        for raw in ({}, {"format": {"format_name": "mp4"}},
                    {"streams": [], "format": {}}, {"streams": "invalid", "format": {"format_name": "mp4"}}):
            with self.assertRaises(ValidationError):
                MediaInfo.model_validate(raw)
        for value in ("nan", "inf", "-1", True, "broken"):
            raw = document()
            raw["format"]["duration"] = value
            with self.subTest(duration=value), self.assertRaises(ValidationError):
                MediaInfo.model_validate(raw)
        for index in (-1, True, "0", 2):
            raw = document()
            raw["streams"][0]["index"] = index
            with self.subTest(index=index), self.assertRaises(ValidationError):
                MediaInfo.model_validate(raw)
        raw = document()
        raw["chapters"][0]["end_time"] = "-1"
        with self.assertRaises(ValidationError):
            MediaInfo.model_validate(raw)

    def test_preserve_all_and_explicit_mapping(self):
        selection = StreamSelectionPolicy().select(MediaInfo.model_validate(document()))
        self.assertEqual(selection.indices, (0, 2, 3, 4, 5, 7, 8))
        self.assertEqual(selection.ffmpeg_arguments(), [
            "-map", "0:0", "-map", "0:2", "-map", "0:3", "-map", "0:4",
            "-map", "0:5", "-map", "0:7", "-map", "0:8", "-map_chapters", "0",
        ])

    def test_first_and_drop_are_explicit_and_cover_art_is_separate(self):
        selection = StreamSelectionPolicy(audio="first", subtitles="none", attachments="none",
                                          pictures="none", data="none", chapters="drop").select(
                                              MediaInfo.model_validate(document()))
        self.assertEqual(selection.indices, (0, 2))
        self.assertEqual(selection.ffmpeg_arguments()[-2:], ["-map_chapters", "-1"])
        selection = StreamSelectionPolicy(video="none").select(MediaInfo.model_validate(document()))
        self.assertIn(7, selection.indices)
        self.assertNotIn(0, selection.indices)

    def test_unknown_streams_and_empty_selection_fail(self):
        raw = document()
        raw["streams"].append({"index": 9, "codec_type": "future"})
        info = MediaInfo.model_validate(raw)
        with self.assertRaises(MediaToolError) as caught:
            StreamSelectionPolicy().select(info)
        self.assertEqual(caught.exception.code, "unsupported_stream_type")
        self.assertNotIn(9, StreamSelectionPolicy(unknown="drop").select(info).indices)
        with self.assertRaises(MediaToolError) as caught:
            StreamSelectionPolicy(video="none", audio="none", subtitles="none", attachments="none",
                                  pictures="none", data="none", unknown="drop").select(info)
        self.assertEqual(caught.exception.code, "empty_stream_selection")
        with self.assertRaises(ValidationError):
            StreamSelectionPolicy(audio="default")


class MediaProbeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        executable = ToolExecutable(Path("/tools/ffprobe"), "test")
        self.tools = MediaTools(executable, executable, frozenset(), frozenset(), frozenset(), frozenset())
        self.runner = AsyncMock()
        self.runner.ffprobe.return_value = ProcessResult(json.dumps(document()).encode(), "", 0)
        self.probe = MediaProbe(self.tools, self.runner)

    async def test_probe_arguments_and_cancellation_forwarding(self):
        event = asyncio.Event()
        info = await self.probe.probe_path("-misleading name", timeout=2, cancellation=event)
        self.assertEqual(info.duration, 12.5)
        self.runner.ffprobe.assert_awaited_once_with(self.tools, [
            "-v", "error", "-of", "json", "-show_format", "-show_streams", "-show_chapters",
            "-i", str(Path("-misleading name").absolute()),
        ], timeout=2, cancellation=event)

    async def test_malformed_output_becomes_structured_error(self):
        for payload in (b"not json", b"{}", b"[]", b"\xff"):
            self.runner.ffprobe.return_value = ProcessResult(payload, "diagnostic", 0)
            with self.assertRaises(MediaToolError) as caught:
                await self.probe.probe_path("file")
            self.assertEqual(caught.exception.code, "invalid_probe_output")
            self.assertEqual(caught.exception.stderr, "diagnostic")
            self.assertIsInstance(caught.exception.__cause__, ValidationError)

    async def test_process_errors_are_preserved(self):
        error = MediaToolError("process_timeout", "timeout")
        self.runner.ffprobe.side_effect = error
        with self.assertRaises(MediaToolError) as caught:
            await self.probe.probe_path("file")
        self.assertIs(caught.exception, error)

    async def test_artifact_lease_lasts_through_probe_and_cleans_on_failure(self):
        with TemporaryDirectory() as directory:
            root = Path(directory)
            store = LocalArtifactStore(root)
            ref = await store.create(b"source", filename="guessed.mp4")
            paths = []

            async def inspect(tools, arguments, **kwargs):
                path = Path(arguments[-1])
                paths.append(path)
                self.assertEqual(path.read_bytes(), b"source")
                path.write_bytes(b"tool modified its input")
                raise asyncio.CancelledError

            self.runner.ffprobe.side_effect = inspect
            with self.assertRaises(asyncio.CancelledError):
                await self.probe.probe_artifact(store, ref)
            self.assertFalse(paths[0].exists())
            self.assertEqual(await store.read(ref), b"source")
            self.assertEqual(len(list(root.iterdir())), 1)

    async def test_precancelled_artifact_probe_never_acquires_input(self):
        store = AsyncMock()
        event = asyncio.Event()
        event.set()
        with self.assertRaises(asyncio.CancelledError):
            await self.probe.probe_artifact(store, None, cancellation=event)
        store.input_path.assert_not_called()
        self.runner.ffprobe.assert_not_called()
