from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel

from mushin.models.node import Port


class RunError(BaseModel):
    """An execution failure suitable for display without parsing a traceback."""

    code: str
    message: str
    node_id: UUID | None = None
    port_name: str | None = None


class PortValueError(ValueError):
    """A runtime value failure associated with one node port."""

    def __init__(self, node_id: UUID, port: Port, message: str) -> None:
        self.node_id = node_id
        self.port_name = port.name
        super().__init__(f"Node {node_id} {port.direction} port {port.name!r} (type {port.type!r}): {message}")
