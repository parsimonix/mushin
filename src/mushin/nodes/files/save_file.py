from __future__ import annotations

from collections.abc import Mapping
from typing import Literal, cast

from pydantic import JsonValue

from mushin.artifacts import LocalArtifactStore
from mushin.models.artifact import ArtifactRef
from mushin.models.node import ExecutionContext, NodeSpec, Port
from mushin.nodes.files.import_file import ImportFileConfig


class SaveFileConfig(ImportFileConfig):
    collision_policy: Literal["error"] = "error"
    overwrite: bool = False


class SaveFile:
    """Materialize an artifact without replacing an existing destination."""

    spec = NodeSpec(
        type="file.save",
        config_model=SaveFileConfig,
        inputs=[
            Port(name="file", direction="input", type="artifact"),
            Port(name="directory", direction="input", type="string"),
        ],
        outputs=[Port(name="path", direction="output", type="string")],
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
        path = await self._artifacts.save(cast(ArtifactRef, inputs["file"]), cast(str, inputs["directory"]), cast(bool, config["overwrite"]))
        return {"path": str(path)}
