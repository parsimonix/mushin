from __future__ import annotations

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel


class NodeRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    BLOCKED = "blocked"
    CANCELLED = "cancelled"


class WorkflowRunStatus(StrEnum):
    PENDING = "pending"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    CANCELLED = "cancelled"


class RunError(BaseModel):
    """An execution failure suitable for display without parsing a traceback."""

    code: str
    message: str
    node_id: UUID | None = None
    port_name: str | None = None


