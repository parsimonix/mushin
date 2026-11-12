from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from pydantic import BaseModel, ConfigDict, JsonValue

from mushin.artifacts import LocalArtifactStore
from mushin.models.node import ExecutionContext, NodeSpec, Port


class ImportFileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ImportFile:
    """Copy a source file into the supplied artifact workspace."""

    spec = NodeSpec(
        type="file.import",
        config_model=ImportFileConfig,
        inputs=[Port(name="source", direction="input", type="string")],
        outputs=[Port(name="file", direction="output", type="artifact")],
    )

    def __init__(self, artifacts: LocalArtifactStore) -> None:
        self._artifacts = artifacts

    async def execute(
        self,
        inputs: Mapping[str, object],
        config: Mapping[str, JsonValue],
        context: ExecutionContext,
    ) -> Mapping[str, object]:
        context.raise_if_cancelled()
        ref = await self._artifacts.import_file(cast(str, inputs["source"]))
        return {"file": ref}
