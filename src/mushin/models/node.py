from __future__ import annotations

from builtins import type as PythonType
from collections.abc import Mapping
from logging import Logger
from typing import Literal, Protocol, Self
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, JsonValue, model_validator


class Node(BaseModel):
    """A configured instance of a registered node type."""

    id: UUID = Field(default_factory=uuid4)
    type: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    config: dict[str, JsonValue] = Field(default_factory=dict)


class PortDefault(BaseModel):
    """An explicit default, including null; absence of this wrapper means no default."""

    value: JsonValue


class Port(BaseModel):
    """A port declaration belonging to a node definition."""

    name: str = Field(min_length=1)
    direction: Literal["input", "output"]
    type: str = Field(min_length=1, description="Stable value type identifier.")
    required: bool = True
    default: PortDefault | None = None

    @model_validator(mode="after")
    def validate_default(self) -> Self:
        if self.direction == "output" and self.default is not None:
            raise ValueError("Only input ports may declare defaults")
        return self


class Edge(BaseModel):
    """A connection from an output port to an input port."""

    source_node: UUID
    source_port: str = Field(min_length=1)
    target_node: UUID
    target_port: str = Field(min_length=1)


class WorkflowInput(BaseModel):
    """Expose a node input port under a workflow-level name."""

    name: str = Field(min_length=1)
    target_node: UUID
    target_port: str = Field(min_length=1)


class WorkflowOutput(BaseModel):
    """Expose a node output port under a workflow-level name."""

    name: str = Field(min_length=1)
    source_node: UUID
    source_port: str = Field(min_length=1)


class Workflow(BaseModel):
    """A serializable graph; graph-level validation is a separate step."""

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1)
    schema_version: int = Field(default=1, ge=1)
    nodes: list[Node] = Field(default_factory=list)
    edges: list[Edge] = Field(default_factory=list)
    inputs: list[WorkflowInput] = Field(default_factory=list)
    outputs: list[WorkflowOutput] = Field(default_factory=list)


class NodeExecutor(Protocol):
    """Runtime behavior supplied by a registered node implementation."""

    async def execute(
        self,
        inputs: Mapping[str, object],
        config: Mapping[str, JsonValue],
        context: ExecutionContext,
    ) -> Mapping[str, object]:
        """Return values keyed by output port name without mutating inputs."""
        ...


class ExecutionContext(Protocol):
    """Runtime services supplied by the runner for one node invocation."""

    @property
    def run_id(self) -> UUID: ...

    @property
    def node_id(self) -> UUID: ...

    @property
    def logger(self) -> Logger: ...

    def raise_if_cancelled(self) -> None:
        """Raise asyncio.CancelledError if cancellation has been requested."""
        ...


class NodeSpec(BaseModel):
    """A registered node type's contract, kept outside saved workflows.

    ``config_model`` validates configuration and supplies its JSON schema via
    ``model_json_schema()``. The model class itself is a runtime reference.
    """

    type: str = Field(min_length=1)
    version: int = Field(default=1, ge=1)
    inputs: list[Port] = Field(default_factory=list)
    outputs: list[Port] = Field(default_factory=list)
    config_model: PythonType[BaseModel]

    @model_validator(mode="after")
    def validate_ports(self) -> Self:
        """Require correctly directed ports with unique names per direction."""
        for direction, ports in (("input", self.inputs), ("output", self.outputs)):
            names: set[str] = set()
            for port in ports:
                if port.direction != direction:
                    raise ValueError(f"Port {port.name!r} must have direction {direction!r}")
                if port.name in names:
                    raise ValueError(f"Duplicate {direction} port name: {port.name!r}")
                names.add(port.name)
        return self
