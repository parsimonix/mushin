from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from pydantic import JsonValue

from mushin.models.artifact import ArtifactRef
from mushin.models.node import ExecutionContext, NodeSpec, Port
from mushin.nodes.files.import_file import ImportFileConfig


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
        self,
        inputs: Mapping[str, object],
        config: Mapping[str, JsonValue],
        context: ExecutionContext,
    ) -> Mapping[str, object]:
        context.raise_if_cancelled()
        ref = cast(ArtifactRef, inputs["file"])
        return {"file": ArtifactRef(content_id=ref.content_id, filename=cast(str, inputs["filename"]))}
