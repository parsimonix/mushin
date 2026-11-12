from __future__ import annotations

from collections.abc import Mapping
from typing import cast

from pydantic import BaseModel, ConfigDict, JsonValue

from mushin.models.node import ExecutionContext, NodeSpec, Port


class ConstantStringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    value: str


class ConstantString:
    """Emit a configured string value."""

    spec = NodeSpec(
        type="string.constant",
        config_model=ConstantStringConfig,
        outputs=[Port(name="value", direction="output", type="string")],
    )

    async def execute(self, inputs: Mapping[str, object], config: Mapping[str, JsonValue], context: ExecutionContext) -> Mapping[str, object]:
        context.raise_if_cancelled()
        return {"value": cast(str, config["value"])}
