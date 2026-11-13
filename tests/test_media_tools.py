import subprocess
import unittest
from pathlib import Path
from unittest.mock import patch

from mushin.media import MediaToolError, MediaTools, ToolConfig


class MediaToolsTests(unittest.TestCase):
    def setUp(self):
        self.which = patch("mushin.media.tools.shutil.which", side_effect=lambda name: f"/tools/{name}").start()
        self.run = patch("mushin.media.tools.subprocess.run", side_effect=self.respond).start()
        self.addCleanup(patch.stopall)

    @staticmethod
    def respond(args, **kwargs):
        option = args[-1]
        if option == "-version":
            name = Path(args[0]).name
            output = f"{name} version test-build Copyright\nconfiguration: test\n"
        else:
            output = {
                "-muxers": "Formats:\n .E = Muxing supported\n  E  matroska Matroska\n  Ed video4linux2,v4l2 device\n",
                "-demuxers": "Formats:\n D  mov,mp4,m4a QuickTime\n D d lavfi Virtual input device\n",
                "-encoders": "Encoders:\n V..... = Video\n V....D libx264 H.264\n A..... aac AAC\n",
                "-decoders": "Decoders:\n VFS..D h264 H.264\n A....D aac AAC\n",
            }[option]
        return subprocess.CompletedProcess(args, 0, stdout=output.encode(), stderr=b"")

    def test_discovery_records_versions_and_separate_capabilities(self):
        tools = MediaTools.discover()
        self.assertEqual(tools.ffmpeg.path, Path("/tools/ffmpeg"))
        self.assertEqual(tools.ffprobe.version, "ffprobe version test-build Copyright")
        self.assertEqual(tools.muxers, {"matroska", "video4linux2", "v4l2"})
        self.assertEqual(tools.demuxers, {"mov", "mp4", "m4a", "lavfi"})
        self.assertEqual(tools.encoders, {"libx264", "aac"})
        self.assertEqual(tools.decoders, {"h264", "aac"})
        tools.require(muxers=["matroska"], demuxers=["mp4"])
        tools.require(encoders=["libx264"], decoders=["h264"])
        self.assertEqual(self.run.call_count, 6)
        for call in self.run.call_args_list:
            self.assertEqual(call.kwargs["stdin"], subprocess.DEVNULL)
            self.assertEqual(call.kwargs["timeout"], 10)

    def test_file_formats_heading_with_crlf_and_aliases(self):
        def respond(args, **kwargs):
            result = self.respond(args, **kwargs)
            if args[-1] in ("-muxers", "-demuxers"):
                result.stdout = result.stdout.replace(b"Formats:", b"File formats:").replace(b"\n", b"\r\n")
            return result

        self.run.side_effect = respond
        tools = MediaTools.discover()
        self.assertEqual(tools.muxers, {"matroska", "video4linux2", "v4l2"})
        self.assertEqual(tools.demuxers, {"mov", "mp4", "m4a", "lavfi"})

    def test_custom_paths_and_timeout(self):
        self.which.side_effect = lambda name: name
        self.run.side_effect = lambda args, **kwargs: self.respond(
            ["/tools/ffprobe" if "probe" in args[0] else "/tools/ffmpeg", *args[1:]], **kwargs)
        tools = MediaTools.discover(ToolConfig(ffmpeg=Path("/custom path/encoder"), ffprobe="/custom/probe", timeout=2))
        self.assertEqual(tools.ffmpeg.path, Path("/custom path/encoder"))
        self.assertEqual(self.run.call_args_list[0].args[0][0], "/custom path/encoder")
        self.assertEqual(self.run.call_args.kwargs["timeout"], 2)

    def test_missing_executable_does_not_launch_tools(self):
        self.which.side_effect = ["/tools/ffmpeg", None]
        with self.assertRaises(MediaToolError) as caught:
            MediaTools.discover()
        self.assertEqual(caught.exception.code, "missing_executable")
        self.assertEqual(caught.exception.executable, "ffprobe")
        self.run.assert_not_called()

    def test_reports_all_missing_requirements(self):
        tools = MediaTools.discover()
        with self.assertRaises(MediaToolError) as caught:
            tools.require(muxers=["mp4"], encoders=["h264"])
        self.assertEqual(caught.exception.code, "missing_capability")
        self.assertIn("muxers: mp4", str(caught.exception))
        self.assertIn("encoders: h264", str(caught.exception))

    def test_launch_timeout_and_exit_errors(self):
        for error, code in ((PermissionError("denied"), "launch_failed"),
                            (subprocess.TimeoutExpired("ffmpeg", 1), "discovery_timeout")):
            with self.subTest(code=code):
                self.run.side_effect = error
                with self.assertRaises(MediaToolError) as caught:
                    MediaTools.discover()
                self.assertEqual(caught.exception.code, code)
        self.run.side_effect = None
        self.run.return_value = subprocess.CompletedProcess([], 1, b"", b"broken build")
        with self.assertRaisesRegex(MediaToolError, "broken build") as caught:
            MediaTools.discover()
        self.assertEqual(caught.exception.code, "command_failed")

    def test_rejects_wrong_tool_or_malformed_listing(self):
        for output in (b"wrong tool", b"ffmpeg version "):
            self.run.side_effect = None
            self.run.return_value = subprocess.CompletedProcess([], 0, output, b"")
            with self.assertRaises(MediaToolError) as caught:
                MediaTools.discover()
            self.assertEqual(caught.exception.code, "invalid_tool_output")
        self.run.side_effect = [self.respond(["ffmpeg", "-version"]),
                                self.respond(["ffprobe", "-version"]),
                                subprocess.CompletedProcess([], 0, b"garbage", b"")]
        with self.assertRaises(MediaToolError) as caught:
            MediaTools.discover()
        self.assertEqual(caught.exception.code, "invalid_tool_output")

    def test_rejects_unbounded_timeouts(self):
        for timeout in (0, -1, float("inf"), float("nan")):
            with self.assertRaises(ValueError):
                ToolConfig(timeout=timeout)
