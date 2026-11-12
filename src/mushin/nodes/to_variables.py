from __future__ import annotations

from collections.abc import Mapping

from pydantic import BaseModel, ConfigDict, JsonValue

from mushin.models.node import ExecutionContext, NodeSpec, Port
from mushin.models.registry import NodeRegistration


class ToVariablesConfig(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class ToVariables:
    """Expose a model's top-level scalar fields as template variables.

    Python-mode dumping preserves field names and avoids silently converting
    nested objects or non-scalar values into strings. Null is unsupported.
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
