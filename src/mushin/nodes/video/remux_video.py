from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator

from mushin.artifacts import LocalArtifactStore
from mushin.exceptions import MediaToolError
from mushin.media import MediaInfo, MediaProbe, MediaTools, ProcessRunner, StreamSelection, StreamSelectionPolicy
from mushin.media.container import matches_container
from mushin.models.artifact import ArtifactRef
from mushin.models.node import ExecutionContext, NodeSpec, Port


class RemuxVideoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    container: Literal["matroska", "mkv", "mp4", "webm"]
    streams: StreamSelectionPolicy = Field(default_factory=StreamSelectionPolicy)
    timeout: float = Field(default=300, gt=0, allow_inf_nan=False)

    @field_validator("container")
    @classmethod
    def normalize_container(cls, value: str) -> str:
        # MKV names the file extension; FFmpeg calls its muxer "matroska".
        return "matroska" if value == "mkv" else value


class RemuxVideo:
    """Copy selected encoded streams into an explicit destination container.

    Timeout applies separately to each probe and the remux process. No stream
    is dropped or transcoded to accommodate container limitations. Completed
    output must pass structural verification before artifact publication.
    """

    spec = NodeSpec(
        type="video.remux", config_model=RemuxVideoConfig,
        inputs=[Port(name="file", direction="input", type="artifact")],
        outputs=[Port(name="file", direction="output", type="artifact")],
    )

    def __init__(self, artifacts: LocalArtifactStore, tools: MediaTools, *, runner: ProcessRunner | None = None) -> None:
        self._artifacts = artifacts
        self._tools = tools
        self._runner = runner if runner is not None else ProcessRunner()
        self._probe = MediaProbe(tools, self._runner)

    async def execute(self, inputs: Mapping[str, object], config: Mapping[str, JsonValue],
                      context: ExecutionContext) -> Mapping[str, object]:
        context.raise_if_cancelled()
        settings = RemuxVideoConfig.model_validate(dict(config))
        ref = cast(ArtifactRef, inputs["file"])
        extension = "mkv" if settings.container == "matroska" else settings.container
        filename = f"{PurePosixPath(ref.filename).stem}.{extension}"
        async with self._artifacts.input_path(ref) as source:
            before = await self._probe.probe_path(source, timeout=settings.timeout, cancellation=context.cancellation)
            selection = settings.streams.select(before)
            # Compare the actual selection: "first" or "drop" may remove
            # nothing, but a matching container alone must not ignore edits.
            unchanged = (selection.indices == tuple(stream.index for stream in before.streams)
                         and (selection.copy_chapters or not before.chapters))
            if unchanged and matches_container(source, before, settings.container):
                context.raise_if_cancelled()
                return {"file": ref}
            # Passing through existing content needs no installed output muxer.
            self._tools.require(muxers=[settings.container])
            async with self._artifacts.output_file(filename=filename) as output:
                arguments = ["-v", "error", "-n", "-i", str(source), *selection.ffmpeg_arguments(),
                             "-map_metadata", "0", "-c", "copy", "-f", settings.container, str(output.path)]
                try:
                    await self._runner.ffmpeg(self._tools, arguments, timeout=settings.timeout, cancellation=context.cancellation,
                        on_progress=lambda seconds: context.report_progress(seconds, before.duration if before.duration and before.duration > 0 else None))
                except MediaToolError as exc:
                    if exc.code != "command_failed":
                        raise
                    raise MediaToolError(
                        "remux_failed",
                        f"Cannot copy selected streams into {settings.container}: {exc.stderr}",
                        executable=exc.executable, returncode=exc.returncode, stderr=exc.stderr,
                    ) from exc
                after = await self._probe.probe_path(output.path, timeout=settings.timeout, cancellation=context.cancellation)
                # A zero exit status alone does not prove every selected stream
                # survived; verify while output is still unpublished staging.
                self._verify(before, after, selection, settings.container)
                # Honor requests arriving during verification before committing.
                context.raise_if_cancelled()
        assert output.ref is not None
        return {"file": output.ref}

    @staticmethod
    def _verify(before: MediaInfo, after: MediaInfo, selection: StreamSelection, container: str) -> None:
        def fail(message: str) -> None:
            raise MediaToolError("remux_verification_failed", message)

        # ffprobe reports shared demuxer aliases for Matroska/WebM and MOV/MP4.
        if container not in after.format.names:
            fail(f"Expected {container} container family; got {after.format.format_name}")
        by_index = {stream.index: stream for stream in before.streams}
        expected = [by_index[index] for index in selection.indices]
        if len(expected) != len(after.streams):
            fail(f"Expected {len(expected)} output streams; got {len(after.streams)}")
        # FFmpeg assigns new indices in mapping order; source indices can have gaps.
        for original, actual in zip(expected, after.streams, strict=True):
            # Opaque Matroska attachments (for example text/plain) legitimately
            # have no codec_name. Their type and descriptive tags still apply.
            missing_codec = original.codec_name is None and original.codec_type != "attachment"
            if missing_codec or original.codec_name != actual.codec_name or original.codec_type != actual.codec_type:
                fail(f"Output stream {actual.index} does not preserve input stream {original.index}'s type and codec")
            if original.codec_type == "attachment":
                for key in ("filename", "mimetype"):
                    if original.tags.get(key) != actual.tags.get(key):
                        fail(f"Output attachment {actual.index} changed {key}")
            if original.is_attached_picture != actual.is_attached_picture:
                fail(f"Output stream {actual.index} changed cover-art disposition")
            for field in ("width", "height", "channels"):
                value = getattr(original, field)
                if value is not None and value != getattr(actual, field):
                    fail(f"Output stream {actual.index} changed {field}")
        chapters = before.chapters if selection.copy_chapters else ()
        if len(chapters) != len(after.chapters):
            fail("Output chapters do not match the selected chapter policy")
        for original, actual in zip(chapters, after.chapters, strict=True):
            # Containers use different time bases; tolerate small rescaling shifts.
            if abs(original.start_time - actual.start_time) > 0.05 or abs(original.end_time - actual.end_time) > 0.05:
                fail("Output chapter timing differs by more than 50 ms")
