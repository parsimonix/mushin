from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from pydantic import JsonValue

from mushin.artifacts import LocalArtifactStore
from mushin.models.artifact import ArtifactRef
from mushin.models.node import ExecutionContext, NodeSpec, Port
from mushin.nodes.files.import_file import ImportFileConfig


class MoveFileConfig(ImportFileConfig):
    create_destination: bool = True


class MoveFile:
    """Move a file within the local filesystem."""

    spec = NodeSpec(
        type="file.move",
        config_model=MoveFileConfig,
        inputs=[
            Port(name="file", direction="input", type="artifact"),
            Port(name="source_directory", direction="input", type="string"),
            Port(name="destination_directory", direction="input", type="string"),
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
        path = await self._artifacts.move(
            cast(ArtifactRef, inputs["file"]),
            cast(str, inputs["source_directory"]),
            cast(str, inputs["destination_directory"]),
            cast(bool, config["create_destination"]),
        )
        return {"path": str(path)}
