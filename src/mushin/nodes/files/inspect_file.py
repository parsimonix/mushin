from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import cast

from pydantic import JsonValue

from mushin.artifacts import LocalArtifactStore
from mushin.models.artifact import ArtifactRef
from mushin.models.node import ExecutionContext, NodeSpec, Port
from mushin.nodes.files.import_file import ImportFileConfig
from mushin.types.file_metadata import FileMetadata


class InspectFile:
    """Extract basic metadata without changing an artifact or its content."""

    spec = NodeSpec(
        type="file.inspect",
        config_model=ImportFileConfig,
        inputs=[Port(name="file", direction="input", type="artifact")],
        outputs=[Port(name="metadata", direction="output", type="file_metadata")],
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
        ref = cast(ArtifactRef, inputs["file"])
        name = PurePosixPath(ref.filename)
        size = await self._artifacts.size(ref)
        return {
            "metadata": FileMetadata(
                filename=ref.filename,
                stem=name.stem,
                extension=name.suffix,
                size_bytes=size,
            )
        }
