from __future__ import annotations

import asyncio
import os
from collections.abc import Mapping
from typing import cast

from pydantic import BaseModel, ConfigDict, JsonValue

from mushin.models.node import ExecutionContext, NodeSpec, Port


class ScanFolderConfig(BaseModel):
    """Scan the immediate folder; no configuration is required."""

    model_config = ConfigDict(extra="forbid", strict=True)


class ScanFolder:
    """List files, folders, and symlinks directly inside a folder.

    Return separate sorted absolute path lists, including hidden entries.
    Symlinks are listed without following their targets, including broken links.
    Special files are excluded. Filesystem errors propagate to the runner.
    The listing reflects the filesystem during scanning, not a frozen snapshot.
    """

    spec = NodeSpec(
        type="folders.scan_folder",
        config_model=ScanFolderConfig,
        inputs=[Port(name="path", direction="input", type="string")],
        outputs=[Port(name=name, direction="output", type="string_list")
                 for name in ("files", "folders", "symlinks")],
    )

    async def execute(self, inputs: Mapping[str, object], config: Mapping[str, JsonValue], context: ExecutionContext) -> Mapping[str, object]:
        context.raise_if_cancelled()
        ScanFolderConfig.model_validate(dict(config))
        path = os.path.abspath(cast(str, inputs["path"]))

        def scan() -> dict[str, list[str]]:
            result: dict[str, list[str]] = {"files": [], "folders": [], "symlinks": []}
            with os.scandir(path) as entries:
                for entry in entries:
                    context.raise_if_cancelled()
                    if entry.is_symlink():
                        result["symlinks"].append(entry.path)
                    elif entry.is_file(follow_symlinks=False):
                        result["files"].append(entry.path)
                    elif entry.is_dir(follow_symlinks=False):
                        result["folders"].append(entry.path)
            return {name: sorted(paths) for name, paths in result.items()}

        result = await asyncio.to_thread(scan)
        context.raise_if_cancelled()
        return result
