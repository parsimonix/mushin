from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from pydantic import BaseModel, ConfigDict, JsonValue

from mushin.models.node import ExecutionContext, NodeSpec, Port


class ConstantNumberConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    value: int | float


class ConstantNumber:
    """Emit a configured numeric value."""

    spec = NodeSpec(
        type="math.constant",
        config_model=ConstantNumberConfig,
        outputs=[Port(name="value", direction="output", type="number")],
    )

    async def execute(self, inputs: Mapping[str, object], config: Mapping[str, JsonValue], context: ExecutionContext) -> Mapping[str, object]:
        context.raise_if_cancelled()
        return {"value": cast(int | float, config["value"])}
