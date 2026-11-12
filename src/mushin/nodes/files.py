from __future__ import annotations

from collections.abc import Mapping
from pathlib import PurePosixPath
from typing import Literal, cast

from pydantic import BaseModel, ConfigDict, JsonValue

from mushin.artifacts import LocalArtifactStore
from mushin.models.artifact import ArtifactRef
from mushin.models.file_metadata import FileMetadata
from mushin.models.node import ExecutionContext, NodeSpec, Port


class ImportFileConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class SaveFileConfig(ImportFileConfig):
    collision_policy: Literal["error"] = "error"


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
        self, inputs: Mapping[str, object], config: Mapping[str, JsonValue], context: ExecutionContext,
    ) -> Mapping[str, object]:
        context.raise_if_cancelled()
        ref = await self._artifacts.import_file(cast(str, inputs["source"]))
        return {"file": ref}


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
        self, inputs: Mapping[str, object], config: Mapping[str, JsonValue], context: ExecutionContext,
    ) -> Mapping[str, object]:
        context.raise_if_cancelled()
        path = await self._artifacts.save(cast(ArtifactRef, inputs["file"]), cast(str, inputs["directory"]))
        return {"path": str(path)}


class RenameFile:
    """Return a reference with a new logical filename and unchanged content.

    No filesystem operation or content lookup occurs. Construct a new model
    so filename validation runs and the original reference stays unchanged.
    """

    spec = NodeSpec(
        type="file.rename",
        config_model=ImportFileConfig,
        inputs=[
            Port(name="file", direction="input", type="artifact"),
            Port(name="filename", direction="input", type="string"),
        ],
        outputs=[Port(name="file", direction="output", type="artifact")],
    )

    async def execute(
        self, inputs: Mapping[str, object], config: Mapping[str, JsonValue], context: ExecutionContext,
    ) -> Mapping[str, object]:
        context.raise_if_cancelled()
        ref = cast(ArtifactRef, inputs["file"])
        return {"file": ArtifactRef(content_id=ref.content_id, filename=cast(str, inputs["filename"]))}


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
        self, inputs: Mapping[str, object], config: Mapping[str, JsonValue], context: ExecutionContext,
    ) -> Mapping[str, object]:
        context.raise_if_cancelled()
        ref = cast(ArtifactRef, inputs["file"])
        name = PurePosixPath(ref.filename)
        size = await self._artifacts.size(ref)
        return {"metadata": FileMetadata(
            filename=ref.filename, stem=name.stem, extension=name.suffix, size_bytes=size,
        )}
