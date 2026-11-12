from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping
from typing import Literal, cast

from guessit import guessit
from pydantic import BaseModel, ConfigDict, JsonValue

from mushin.models.artifact import ArtifactRef
from mushin.models.node import ExecutionContext, NodeSpec, Port
from mushin.types.video_metadata import VideoMetadata


class GuessVideoConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    type: Literal["movie", "episode"] | None = None


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
        options: dict[str, object] = {"no_user_config": True}
        if config.get("type") is not None:
            options["type"] = config["type"]
        metadata = await asyncio.to_thread(self._guess, ref.filename, options)
        context.raise_if_cancelled()
        return {"metadata": metadata}

    @staticmethod
    def _guess(filename: str, options: dict[str, object]) -> VideoMetadata:
        matches = guessit(filename, options)
        # GuessIt includes date and babelfish objects; keep scalar/list structure
        # while converting those leaf objects to their text representations.
        return VideoMetadata.model_validate_json(json.dumps(dict(matches), default=str))
