from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

from mushin.enums import NodeRunStatus, WorkflowRunStatus
from mushin.exceptions import RunError


class StateEvent(BaseModel):
    """A state notification captured at the time of a transition.

    Events describe the new state. Consumers should use delivery order rather
    than timestamps to order events within a run.
    """

    model_config = ConfigDict(frozen=True)

    # Wall-clock timestamps can tie or jump; delivery order is the run's ordering.
    timestamp: datetime = Field(default_factory=lambda: datetime.now(UTC))
    run_id: UUID
    error: RunError | None = None


class RunStateEvent(StateEvent):
    """A workflow run entered a new state."""

    type: Literal["run_state_changed"] = "run_state_changed"
    status: WorkflowRunStatus


class NodeStateEvent(StateEvent):
    """A node entered a new state, including blocked or cancelled."""

    type: Literal["node_state_changed"] = "node_state_changed"
    node_id: UUID
    status: NodeRunStatus


class NodeProgressEvent(StateEvent):
    """Work performed within a running node; percentage may be unknown."""

    type: Literal["node_progress"] = "node_progress"
    node_id: UUID
    completed: float = Field(ge=0, allow_inf_nan=False)
    total: float | None = Field(default=None, gt=0, allow_inf_nan=False)
    unit: str = "seconds"

    @property
    def percentage(self) -> float | None:
        return min(100.0, self.completed / self.total * 100) if self.total is not None else None


# The discriminator preserves each event's status type during JSON parsing.
ExecutionEvent = Annotated[RunStateEvent | NodeStateEvent | NodeProgressEvent, Field(discriminator="type")]
