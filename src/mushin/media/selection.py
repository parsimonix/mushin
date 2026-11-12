from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict

from mushin.exceptions import MediaToolError
from mushin.media.probe import MediaInfo

SelectionMode = Literal["all", "first", "none"]


@dataclass(frozen=True)
class StreamSelection:
    """Absolute input stream indices in probe order and explicit chapter policy."""

    indices: tuple[int, ...]
    copy_chapters: bool

    def ffmpeg_arguments(self) -> list[str]:
        arguments = []
        for index in self.indices:
            # Explicit absolute maps bypass FFmpeg's automatic stream selection.
            arguments.extend(["-map", f"0:{index}"])
        # Omitting this option would allow FFmpeg to copy chapters automatically.
        arguments.extend(["-map_chapters", "0" if self.copy_chapters else "-1"])
        return arguments


class StreamSelectionPolicy(BaseModel):
    """Preserve all recognized streams by default, including cover art and data.

    `first` means first in probe order, not default disposition or language.
    Cover art is separate from ordinary video; attachments are separate again.
    Unknown types fail unless explicitly dropped. Unsupported destination
    containers must fail later rather than silently changing this selection.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    video: SelectionMode = "all"
    audio: SelectionMode = "all"
    subtitles: SelectionMode = "all"
    attachments: SelectionMode = "all"
    pictures: SelectionMode = "all"
    data: SelectionMode = "all"
    unknown: Literal["error", "drop"] = "error"
    chapters: Literal["copy", "drop"] = "copy"

    def select(self, info: MediaInfo) -> StreamSelection:
        seen = set()
        indices = []
        categories = {"video": "video", "audio": "audio", "subtitle": "subtitles", "attachment": "attachments", "data": "data"}
        for stream in info.streams:
            # Cover art is a video stream, but must not consume the "first video" slot.
            category = "pictures" if stream.is_attached_picture else categories.get(stream.codec_type)
            if category is None:
                if self.unknown == "error":
                    raise MediaToolError("unsupported_stream_type", f"Stream {stream.index} has unrecognized type {stream.codec_type!r}")
                continue
            mode = getattr(self, category)
            if mode == "all" or (mode == "first" and category not in seen):
                indices.append(stream.index)
            seen.add(category)
        if not indices:
            # With no -map arguments FFmpeg would silently use automatic selection.
            raise MediaToolError("empty_stream_selection", "Stream policy selected no streams")
        return StreamSelection(tuple(indices), self.chapters == "copy")
