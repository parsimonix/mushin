"""Tiny generated media fixtures; no downloaded or committed binary assets."""

import unittest
from pathlib import Path

from mushin.exceptions import MediaToolError
from mushin.media import ProcessRunner


class MediaFixtures:
    def __init__(self, root, tools):
        self.root = root
        self.tools = tools
        self.runner = ProcessRunner()

    def require(self, **requirements):
        try:
            self.tools.require(**requirements)
        except MediaToolError as exc:
            # Missing optional fixture support is a skip; actual tool failures
            # must still fail the test rather than hide a regression.
            if exc.code != "missing_capability":
                raise
            raise unittest.SkipTest(str(exc)) from exc

    async def video(self, name, *, container="matroska", video_codec="mpeg4", audio_codec="aac", audio_tracks=1,
                    extras=False):
        self.require(muxers=[container], demuxers=["lavfi"],
                     encoders=[video_codec, *([audio_codec] if audio_tracks else []), *(["srt"] if extras else [])])
        path = self.root / name
        arguments = ["-v", "error", "-f", "lavfi", "-i", "color=size=32x32:rate=5:duration=1"]
        # Distinct tones and language tags make audio tracks distinguishable.
        for track in range(audio_tracks):
            arguments += ["-f", "lavfi", "-i", f"sine=frequency={440 + track * 220}:sample_rate=48000:duration=1"]
        if extras:
            subtitles = self.root / "captions.srt"
            subtitles.write_text("1\n00:00:00,000 --> 00:00:00,800\nFixture caption\n", encoding="utf-8")
            chapters = self.root / "chapters.ffmeta"
            chapters.write_text(";FFMETADATA1\n[CHAPTER]\nTIMEBASE=1/1000\nSTART=0\nEND=1000\ntitle=Opening\n", encoding="utf-8")
            attachment = self.root / "notes.txt"
            attachment.write_text("Fixture attachment contents\n", encoding="utf-8")
            arguments += ["-i", str(subtitles), "-f", "ffmetadata", "-i", str(chapters)]
        arguments += ["-map", "0:v:0"]
        for track in range(audio_tracks):
            arguments += ["-map", f"{track + 1}:a:0", f"-metadata:s:a:{track}", f"language={('eng', 'fra')[track % 2]}"]
        if extras:
            # Input zero is video, followed by audio inputs, subtitles, then chapters.
            arguments += ["-map", f"{audio_tracks + 1}:s:0", "-c:s", "srt",
                          "-map_chapters", str(audio_tracks + 2), "-attach", str(attachment),
                          "-metadata:s:t:0", "mimetype=text/plain", "-metadata:s:t:0", "filename=notes.txt"]
        # Tiny fixtures need only one encoding thread, independent of host CPU count.
        arguments += ["-c:v", video_codec, "-threads:v", "1"]
        if audio_tracks:
            arguments += ["-c:a", audio_codec]
        arguments += ["-f", container, str(path)]
        await self.runner.ffmpeg(self.tools, arguments, timeout=15)
        return path

    async def with_cover(self):
        self.require(encoders=["png"], muxers=["image2", "mp4"])
        base = await self.video("without-cover.mp4", container="mp4")
        cover = self.root / "cover.png"
        await self.runner.ffmpeg(self.tools, ["-v", "error", "-f", "lavfi", "-i", "color=c=red:size=16x16",
                                             "-frames:v", "1", "-c:v", "png", "-threads:v", "1", str(cover)], timeout=15)
        result = self.root / "with-cover.mp4"
        # The disposition distinguishes embedded art from a second playable video.
        await self.runner.ffmpeg(self.tools, ["-v", "error", "-i", str(base), "-i", str(cover),
                                             "-map", "0", "-map", "1:v:0", "-c", "copy",
                                             "-disposition:v:1", "attached_pic", str(result)], timeout=15)
        return result
