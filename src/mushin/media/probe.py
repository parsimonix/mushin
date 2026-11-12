from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Annotated

from pydantic import BaseModel, BeforeValidator, ConfigDict, Field, StrictInt, ValidationError, model_validator

from mushin.artifacts import LocalArtifactStore
from mushin.exceptions import MediaToolError
from mushin.media.process import ProcessRunner
from mushin.media.tools import MediaTools
from mushin.models.artifact import ArtifactRef


def _unknown(value: object) -> object:
    if value == "N/A":
        return None
    if isinstance(value, bool):
        # Pydantic wraps ValueError as a validation failure; TypeError escapes
        # validation and would bypass the service's structured probe error.
        raise ValueError("Time values cannot be booleans")
    return value


# ffprobe emits times as numeric strings or N/A; reject non-finite values so
# duration comparisons and progress calculations remain meaningful.
Seconds = Annotated[float, Field(allow_inf_nan=False), BeforeValidator(_unknown)]
Duration = Annotated[float | None, Field(ge=0, allow_inf_nan=False), BeforeValidator(_unknown)]
# bool is an int subclass in Python, but never a valid stream index or flag.
Index = Annotated[StrictInt, Field(ge=0)]
Flag = Annotated[StrictInt, Field(ge=0, le=1)]


class ProbeModel(BaseModel):
    """Known ffprobe fields; additional version-specific fields are ignored."""

    model_config = ConfigDict(frozen=True, extra="ignore")


class MediaStream(ProbeModel):
    index: Index
    codec_type: str = Field(min_length=1)
    codec_name: str | None = None
    duration: Duration = None
    width: Index | None = None
    height: Index | None = None
    channels: Index | None = None
    tags: dict[str, str] = Field(default_factory=dict)
    disposition: dict[str, Flag] = Field(default_factory=dict)

    @property
    def is_attached_picture(self) -> bool:
        return self.codec_type == "video" and self.disposition.get("attached_pic", 0) == 1


class MediaFormat(ProbeModel):
    format_name: str = Field(min_length=1)
    duration: Duration = None
    tags: dict[str, str] = Field(default_factory=dict)

    @property
    def names(self) -> tuple[str, ...]:
        return tuple(self.format_name.split(","))


class MediaChapter(ProbeModel):
    id: StrictInt
    start_time: Seconds
    end_time: Seconds
    tags: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_interval(self) -> MediaChapter:
        if self.end_time < self.start_time:
            raise ValueError("Chapter end must not precede its start")
        return self


class MediaInfo(ProbeModel):
    streams: tuple[MediaStream, ...]
    format: MediaFormat
    chapters: tuple[MediaChapter, ...] = ()

    @model_validator(mode="after")
    def unique_stream_indices(self) -> MediaInfo:
        # Stream selection maps by absolute index, so duplicates are ambiguous.
        indices = [stream.index for stream in self.streams]
        if len(indices) != len(set(indices)):
            raise ValueError("Duplicate stream indices in probe output")
        return self

    @property
    def duration(self) -> float | None:
        """Container-reported seconds; unknown stays unknown."""
        # Streams can start at different offsets; their longest duration is
        # not necessarily the container duration.
        return self.format.duration


class MediaProbe:
    """Probe actual content through the managed process runner."""

    def __init__(self, tools: MediaTools, runner: ProcessRunner | None = None) -> None:
        self.tools = tools
        self.runner = runner if runner is not None else ProcessRunner()

    async def probe_path(self, path: str | Path, *, timeout: float = 30, cancellation: asyncio.Event | None = None) -> MediaInfo:
        """Probe a local file; its owner keeps it alive until this await ends."""
        # An absolute path cannot be mistaken for a leading option or URL scheme.
        result = await self.runner.ffprobe(
            self.tools,
            ["-v", "error", "-of", "json", "-show_format", "-show_streams", "-show_chapters", "-i", str(Path(path).absolute())],
            timeout=timeout,
            cancellation=cancellation,
        )
        try:
            return MediaInfo.model_validate_json(result.stdout)
        except ValidationError as exc:
            raise MediaToolError(
                "invalid_probe_output",
                "ffprobe returned invalid or incomplete media metadata",
                executable=str(self.tools.ffprobe.path),
                returncode=result.returncode,
                stderr=result.stderr,
            ) from exc

    async def probe_artifact(self, store: LocalArtifactStore, ref: ArtifactRef, *, timeout: float = 30, cancellation: asyncio.Event | None = None) -> MediaInfo:
        if cancellation is not None and cancellation.is_set():
            raise asyncio.CancelledError
        async with store.input_path(ref) as path:
            return await self.probe_path(path, timeout=timeout, cancellation=cancellation)
