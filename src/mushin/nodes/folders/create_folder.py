from __future__ import annotations

import os
from collections.abc import Mapping
from typing import cast

from pydantic import BaseModel, ConfigDict, JsonValue

from mushin.models.node import ExecutionContext, NodeSpec, Port


class CreateFolderConfig(BaseModel):
    """Configuration for the CreateFolder node."""

    model_config = ConfigDict(extra="forbid")


class CreateFolder:
    """Create a folder at the specified path."""

    spec = NodeSpec(
        type="folders.create_folder",
        config_model=CreateFolderConfig,
        inputs=[Port(name="path", direction="input", type="string")],
        outputs=[Port(name="success", direction="output", type="boolean")],
    )

    async def execute(self, inputs: Mapping[str, object], config: Mapping[str, JsonValue], context: ExecutionContext) -> Mapping[str, object]:
        context.raise_if_cancelled()
        path = cast(str, inputs["path"])
        try:
            os.makedirs(path, exist_ok=True)
            return {"success": True}
        except Exception as e:
            context.logger.error(f"Failed to create folder at {path}: {e}")
            return {"success": False}
