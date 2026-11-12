from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import Mapping
from typing import Literal, cast

from guessit import guessit
from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from mushin.models.artifact import ArtifactRef
from mushin.models.node import ExecutionContext, NodeSpec, Port
from mushin.types.video_metadata import VideoMetadata


class GuessVideoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["movie", "episode"] | None = None
    series_title: str | None = Field(default=None, min_length=1)

    @field_validator("series_title")
    @classmethod
    def validate_series_title(cls, value: str | None) -> str | None:
        if value is not None and (not value.strip() or "/" in value or "\\" in value):
            raise ValueError("Series title must be nonblank and contain no path separators")
        return value

    @model_validator(mode="after")
    def validate_series_type(self) -> GuessVideoConfig:
        if self.series_title is not None and self.type == "movie":
            raise ValueError("Series title is only supported for episodes")
        return self


class GuessVideo:
    """Infer video metadata from an artifact's logical filename using GuessIt.

    No content lookup is required. Parsing runs in a worker thread; cancelling
    the await does not interrupt that thread, but parsing has no side effects.
    """

    spec = NodeSpec(
        type="video.guess",
        config_model=GuessVideoConfig,
        inputs=[Port(name="file", direction="input", type="artifact")],
        outputs=[Port(name="metadata", direction="output", type="video_metadata")],
    )

    async def execute(
        self,
        inputs: Mapping[str, object],
        config: Mapping[str, JsonValue],
        context: ExecutionContext,
    ) -> Mapping[str, object]:
        context.raise_if_cancelled()
        ref = cast(ArtifactRef, inputs["file"])
        settings = GuessVideoConfig.model_validate(dict(config))
        # A workflow's guesses must not depend on the host user's GuessIt settings.
        options: dict[str, object] = {"no_user_config": True}
        if settings.type is not None:
            options["type"] = settings.type
        filename = ref.filename
        if settings.series_title is not None:
            # GuessIt treats the sole filename title as the series title when
            # the series is absent. A synthetic folder supplies that context
            # without renaming the artifact or requiring a real directory.
            filename = f"{settings.series_title}/{filename}"
            options["type"] = "episode"
        metadata = await asyncio.to_thread(self._guess, filename, options)
        context.raise_if_cancelled()
        return {"metadata": metadata}

    @staticmethod
    def _guess(filename: str, options: dict[str, object]) -> VideoMetadata:
        matches = guessit(filename, options)
        # GuessIt includes date and babelfish objects; keep scalar/list structure
        # while converting those leaf objects to their text representations.
        metadata = VideoMetadata.model_validate_json(json.dumps(dict(matches), default=str))
        logging.getLogger(__name__).debug("GuessIt parsed %r as %r", filename, metadata)
        return metadata
