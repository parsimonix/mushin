from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, JsonValue

from mushin.models.node import ExecutionContext, NodeSpec, Port
from mushin.models.registry import NodeRegistration


class ToVariablesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    fields: list[str] | None = None
    omit_none: bool = False
    list_separator: str | None = None


class ToVariables:
    """Expose a model's top-level scalar fields as template variables.

    Python-mode dumping preserves field names and avoids silently converting
    nested objects into strings. Configuration can select fields, omit nulls,
    and join scalar lists. Defaults retain strict scalar-only behavior.
    """

    def __init__(self, model: type[BaseModel]) -> None:
        self._model = model

    async def execute(
        self, inputs: Mapping[str, object], config: Mapping[str, JsonValue], context: ExecutionContext,
    ) -> Mapping[str, object]:
        context.raise_if_cancelled()
        value = inputs["value"]
        if not isinstance(value, self._model):
            raise TypeError(f"ToVariables requires {self._model.__name__}; check the registered input type")
        variables = value.model_dump(mode="python", by_alias=False)
        if not isinstance(variables, dict):
            raise ValueError("ToVariables requires a model that dumps to a field mapping")
        options = ToVariablesConfig.model_validate(dict(config))
        # Select and omit before rejecting nested values, so unused metadata
        # fields cannot prevent rendering a valid subset of scalar variables.
        if options.fields is not None:
            missing = [name for name in options.fields if name not in variables]
            if missing:
                raise ValueError(f"Unknown variable fields: {missing}")
            variables = {name: variables[name] for name in options.fields}
        if options.omit_none:
            variables = {name: item for name, item in variables.items() if item is not None}
        if options.list_separator is not None:
            # Join only scalar lists; nested structures must not become opaque text.
            variables = {
                name: options.list_separator.join(str(element) for element in item)
                if isinstance(item, list) and all(isinstance(element, (str, int, float, bool)) for element in item)
                else item
                for name, item in variables.items()
            }
        unsupported = [name for name, item in variables.items() if not isinstance(item, (str, int, float, bool))]
        if unsupported:
            raise ValueError(f"Variables must be non-null scalar values; unsupported fields: {unsupported}")
        return {"variables": variables}


def to_variables_registration(
    model: type[BaseModel], *, input_type: str, node_type: str | None = None, version: int = 1,
) -> NodeRegistration:
    """Build a typed adapter spec and executor together.

    input_type must already resolve to model in the workflow's TypeRegistry.
    Persisted node IDs derive from the stable type ID, never a Python class
    name. Supply node_type/version when independently versioning an adapter.
    """
    if not input_type.strip():
        raise ValueError("Input type must not be empty or whitespace")
    if not isinstance(model, type) or not issubclass(model, BaseModel):
        raise TypeError("ToVariables requires a Pydantic model class")
    return NodeRegistration(
        spec=NodeSpec(
            type=f"variables.from.{input_type}" if node_type is None else node_type,
            version=version,
            config_model=ToVariablesConfig,
            inputs=[Port(name="value", direction="input", type=input_type)],
            outputs=[Port(name="variables", direction="output", type="template_variables")],
        ),
        executor=ToVariables(model),
    )
