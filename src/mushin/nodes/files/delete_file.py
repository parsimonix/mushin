from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from pydantic import JsonValue

from mushin.artifacts import LocalArtifactStore
from mushin.models.artifact import ArtifactRef
from mushin.models.node import ExecutionContext, NodeSpec, Port
from mushin.nodes.files.import_file import ImportFileConfig


class DeleteFileConfig(ImportFileConfig):
    allow_missing: bool = True


class DeleteFile:
    """Delete a file from the local filesystem."""

    spec = NodeSpec(
        type="file.delete",
        config_model=DeleteFileConfig,
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
        path = await self._artifacts.delete(cast(ArtifactRef, inputs["file"]), cast(str, inputs["directory"]), cast(bool, config["allow_missing"]))
        return {"path": str(path)}
