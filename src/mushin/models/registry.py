from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from pydantic import TypeAdapter

from mushin.models.artifact import ArtifactRef
from mushin.models.node import NodeExecutor, NodeSpec
from mushin.types.file_metadata import FileMetadata
from mushin.types.video_metadata import VideoMetadata


@dataclass(frozen=True)
class NodeRegistration:
    """A node specification paired with its runtime implementation."""

    spec: NodeSpec
    executor: NodeExecutor


class NodeRegistry:
    """Resolve exact node type/version pairs to registered implementations."""

    def __init__(self) -> None:
        self._registrations: dict[tuple[str, int], NodeRegistration] = {}

    def register(self, spec: NodeSpec, executor: NodeExecutor) -> None:
        """Register a pair; raise ValueError if its type/version already exists."""
        key = (spec.type, spec.version)
        if key in self._registrations:
            raise ValueError(f"Node type {spec.type!r} version {spec.version} is already registered")
        self._registrations[key] = NodeRegistration(spec=spec, executor=executor)

    def resolve(self, node_type: str, version: int) -> NodeRegistration:
        """Return a registration; raise KeyError if its type/version is unknown."""
        try:
            return self._registrations[(node_type, version)]
        except KeyError:
            raise KeyError(f"Unknown node type {node_type!r} version {version}") from None

    def list_specs(self) -> tuple[NodeSpec, ...]:
        """Return all registered specifications in registration order."""
        return tuple(registration.spec for registration in self._registrations.values())


class TypeRegistry:
    """Map stable port type identifiers to Pydantic value validators.

    Structured models and collections use the same interface as primitives.
    """

    def __init__(self) -> None:
        self._adapters: dict[str, TypeAdapter[Any]] = {}

    def register(self, type_id: str, adapter: TypeAdapter[Any]) -> None:
        """Register an adapter; raise ValueError for empty or duplicate IDs."""
        if not type_id.strip():
            raise ValueError("Type identifier must not be empty or whitespace")
        if type_id in self._adapters:
            raise ValueError(f"Type {type_id!r} is already registered")
        self._adapters[type_id] = adapter

    def resolve(self, type_id: str) -> TypeAdapter[Any]:
        """Return an adapter; raise KeyError for an unknown type identifier."""
        try:
            return self._adapters[type_id]
        except KeyError:
            raise KeyError(f"Unknown value type {type_id!r}") from None

    def validate(self, type_id: str, value: object) -> object:
        """Validate with the registered adapter using strict=True.

        Return the validated value. Raise KeyError for unknown types or
        pydantic.ValidationError for invalid values. Callers attach node and
        port identifiers when reporting failures.
        """
        # Edges must not silently turn numeric strings or booleans into numbers.
        return self.resolve(type_id).validate_python(value, strict=True)


def create_default_type_registry() -> TypeRegistry:
    """Return a fresh registry of primitives, artifact references, and file metadata.

    ``number`` accepts integers and floats, excluding booleans. Primitive types
    do not accept null; register an explicit nullable type when needed.
    """
    registry = TypeRegistry()
    registry.register("string", TypeAdapter(str))
    registry.register("string_list", TypeAdapter(list[str]))
    registry.register("integer", TypeAdapter(int))
    registry.register("number", TypeAdapter(int | float))
    registry.register("boolean", TypeAdapter(bool))
    registry.register("artifact", TypeAdapter(ArtifactRef))
    registry.register("file_metadata", TypeAdapter(FileMetadata))
    registry.register("video_metadata", TypeAdapter(VideoMetadata))
    registry.register("template_variables", TypeAdapter(dict[str, str | int | float | bool]))
    return registry
