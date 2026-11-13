from __future__ import annotations

import os
from collections.abc import Mapping
from typing import cast

from pydantic import BaseModel, ConfigDict, JsonValue

from mushin.models.node import ExecutionContext, NodeSpec, Port


class DeleteFolderConfig(BaseModel):
    """Configuration for the DeleteFolder node."""

    model_config = ConfigDict(extra="forbid")


class DeleteFolder:
    """Delete a folder at the specified path."""

    spec = NodeSpec(
        type="folders.delete_folder",
        config_model=DeleteFolderConfig,
        inputs=[Port(name="path", direction="input", type="string")],
        outputs=[Port(name="success", direction="output", type="boolean")],
    )

    async def execute(self, inputs: Mapping[str, object], config: Mapping[str, JsonValue], context: ExecutionContext) -> Mapping[str, object]:
        context.raise_if_cancelled()
        path = cast(str, inputs["path"])
        try:
            os.rmdir(path)
            return {"success": True}
        except Exception as e:
            context.logger.error(f"Failed to delete folder at {path}: {e}")
            return {"success": False}
