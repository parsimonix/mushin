from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from pydantic import BaseModel, ConfigDict, JsonValue

from mushin.models.node import ExecutionContext, NodeSpec, Port


class AddConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Add:
    """Add two numeric input values."""

    spec = NodeSpec(
        type="math.add",
        config_model=AddConfig,
        inputs=[Port(name="a", direction="input", type="number"), Port(name="b", direction="input", type="number")],
        outputs=[Port(name="sum", direction="output", type="number")],
    )

    async def execute(self, inputs: Mapping[str, object], config: Mapping[str, JsonValue], context: ExecutionContext) -> Mapping[str, object]:
        context.raise_if_cancelled()
        return {"sum": cast(int | float, inputs["a"]) + cast(int | float, inputs["b"])}
