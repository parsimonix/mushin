from __future__ import annotations

from collections.abc import Mapping
from string import Formatter
from typing import cast

from pydantic import BaseModel, ConfigDict, JsonValue, field_validator

from mushin.models.node import ExecutionContext, NodeSpec, Port


class TemplatedStringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    template: str

    @field_validator("template")
    @classmethod
    def validate_template(cls, value: str) -> str:
        # Allow plain named placeholders and escaped {{ / }} braces only.
        for _, name, format_spec, conversion in Formatter().parse(value):
            if name is not None and (not name.isidentifier() or format_spec or conversion):
                raise ValueError("Use plain named placeholders such as {title}; formatting and attribute access are unsupported")
        return value


class TemplatedString:
    """Generate a string from a template and named scalar values.

    Extra variables are ignored. Missing variables fail execution. This node
    performs interpolation only; consumers validate domain-specific constraints.
    """

    spec = NodeSpec(
        type="string.template",
        config_model=TemplatedStringConfig,
        inputs=[Port(name="variables", direction="input", type="template_variables")],
        outputs=[Port(name="text", direction="output", type="string")],
    )

    async def execute(
        self, inputs: Mapping[str, object], config: Mapping[str, JsonValue], context: ExecutionContext,
    ) -> Mapping[str, object]:
        context.raise_if_cancelled()
        template = cast(str, config["template"])
        variables = cast(dict[str, str | int | float | bool], inputs["variables"])
        try:
            text = template.format_map(variables)
        except KeyError as exc:
            raise ValueError(f"Missing template variable {exc.args[0]!r}") from exc
        return {"text": text}
