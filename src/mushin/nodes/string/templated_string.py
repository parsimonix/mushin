from __future__ import annotations

from collections.abc import Mapping
from string import Formatter
from typing import cast

from pydantic import BaseModel, ConfigDict, Field, JsonValue, field_validator, model_validator

from mushin.models.node import ExecutionContext, NodeSpec, Port


class TemplatedStringConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    template: str
    defaults: dict[str, str | int | float | bool] = Field(default_factory=dict)
    optional_sections: dict[str, str] = Field(default_factory=dict)

    @field_validator("template")
    @classmethod
    def validate_template(cls, value: str) -> str:
        # Allow one bounded integer format for episode numbering; arbitrary
        # specs can request huge padding or nested field evaluation.
        for _, name, format_spec, conversion in Formatter().parse(value):
            if name is not None and (not name.isidentifier() or format_spec not in ("", "02d") or conversion):
                raise ValueError("Use named placeholders such as {title} or {season:02d}; other formatting, conversions, and attribute access are unsupported")
        return value

    @model_validator(mode="after")
    def validate_sections(self) -> TemplatedStringConfig:
        for name, template in self.optional_sections.items():
            if not name.isidentifier():
                raise ValueError("Optional section names must be valid identifiers")
            self.validate_template(template)
            # Sections depend only on input variables, never on evaluation order.
            references = {field for _, field, _, _ in Formatter().parse(template) if field is not None}
            if references.intersection(self.optional_sections):
                raise ValueError("Optional sections cannot reference other optional sections")
        if self.defaults.keys() & self.optional_sections.keys():
            raise ValueError("Defaults cannot use optional section names")
        return self


class TemplatedString:
    """Generate a string from a template and named scalar values.

    Extra variables are ignored. Missing variables fail execution. This node
    performs interpolation only; consumers validate domain-specific constraints.
    Integer placeholders may use :02d for a minimum width of two digits.
    Defaults fill missing variables; optional sections omit their punctuation
    along with unavailable values. Other missing placeholders still fail.
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
        settings = TemplatedStringConfig.model_validate(dict(config))
        supplied = cast(dict[str, str | int | float | bool], inputs["variables"])
        # Preserve existing values, including zero and False, without mutating inputs.
        variables = {**settings.defaults, **supplied}
        if variables.keys() & settings.optional_sections.keys():
            raise ValueError("Input variables cannot use optional section names")
        for name, section in settings.optional_sections.items():
            fields = [field for _, field, _, _ in Formatter().parse(section) if field is not None]
            # Empty strings count as unavailable; zero and False are real values.
            available = all(field in variables and variables[field] != "" for field in fields)
            variables[name] = section.format_map(variables) if available else ""
        try:
            text = settings.template.format_map(variables)
        except KeyError as exc:
            raise ValueError(f"Missing template variable {exc.args[0]!r}") from exc
        return {"text": text}
