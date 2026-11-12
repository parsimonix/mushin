from __future__ import annotations

from asyncio import CancelledError, Event, current_task
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import UTC, datetime
from enum import StrEnum
from graphlib import TopologicalSorter
from heapq import heappop, heappush
from logging import Logger, getLogger
from types import MappingProxyType
from uuid import UUID, uuid4

from pydantic import BaseModel, Field, ValidationError

from mushin.models.node import Node, NodeSpec, Port, Workflow
from mushin.models.registry import NodeRegistration, NodeRegistry, TypeRegistry, create_default_type_registry
from mushin.models.validation import ValidationIssue, WorkflowValidator

logger = getLogger(__name__)


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


class PortValueError(ValueError):
    """A runtime value failure associated with one node port."""

    def __init__(self, node_id: UUID, port: Port, message: str) -> None:
        self.node_id = node_id
        self.port_name = port.name
        super().__init__(f"Node {node_id} {port.direction} port {port.name!r} (type {port.type!r}): {message}")


class NodeRun(BaseModel):
    """The state and results of one node invocation.

    Outputs are runtime values; persistence will need explicit value encoding.
    """

    node_id: UUID
    status: NodeRunStatus = NodeRunStatus.PENDING
    outputs: dict[str, object] = Field(default_factory=dict)
    error: RunError | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


class WorkflowRun(BaseModel):
    """A run record containing a workflow snapshot and per-node results."""

    id: UUID = Field(default_factory=uuid4)
    workflow: Workflow
    status: WorkflowRunStatus = WorkflowRunStatus.PENDING
    nodes: dict[UUID, NodeRun] = Field(default_factory=dict)
    validation_issues: list[ValidationIssue] = Field(default_factory=list)
    error: RunError | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None


@dataclass(frozen=True)
class NodeExecutionContext:
    """Concrete services satisfying the ExecutionContext protocol."""

    run_id: UUID
    node_id: UUID
    logger: Logger
    cancellation: Event = field(default_factory=Event)

    def raise_if_cancelled(self) -> None:
        """Raise asyncio.CancelledError when the shared cancellation event is set."""
        if self.cancellation.is_set():
            raise CancelledError


class WorkflowRunner:
    """Execute validated workflows sequentially in dependency order."""

    def __init__(self, registry: NodeRegistry, type_registry: TypeRegistry | None = None) -> None:
        self._registry = registry
        self._type_registry = type_registry if type_registry is not None else create_default_type_registry()
        self._validator = WorkflowValidator(registry, self._type_registry)

    async def run(self, workflow: Workflow, *, cancellation: Event | None = None) -> WorkflowRun:
        """Snapshot and validate the graph, then execute its nodes.

        Invalid graphs return a failed run with validation issues and execute
        no nodes. Gather inputs from upstream outputs and validate port names,
        required values, and runtime types before and after execution.

        On node failure, block its dependents and cancel other unstarted nodes.
        A supplied event requests cooperative cancellation between and within
        node executions. Record UTC timestamps and structured errors.
        """
        snapshot = workflow.model_copy(deep=True)
        run = WorkflowRun(workflow=snapshot, started_at=self._now())
        logger.info("Run %s: starting workflow %s (%s)", run.id, snapshot.name, snapshot.id)
        logger.debug("Run %s: snapshot contains %d nodes and %d edges", run.id, len(snapshot.nodes), len(snapshot.edges))
        run.nodes = {node.id: NodeRun(node_id=node.id) for node in snapshot.nodes}
        logger.info("Run %s: validating workflow", run.id)
        run.validation_issues = self._validator.validate(snapshot)
        if run.validation_issues:
            logger.info("Run %s: validation failed with %d issues", run.id, len(run.validation_issues))
            for issue in run.validation_issues:
                logger.debug("Run %s: validation issue %s, node=%s port=%s edge=%s", run.id, issue.code, issue.node_id, issue.port_name, issue.edge_index)
            run.status = WorkflowRunStatus.FAILED
            run.error = RunError(code="invalid_workflow", message="Workflow validation failed")
            self._finish_pending(run)
            run.finished_at = self._now()
            self._log_completion(run)
            return run

        logger.info("Run %s: workflow validation passed", run.id)
        # Freeze specifications before the first await; executor instances remain shared.
        registrations = self._resolve_nodes(snapshot)
        logger.debug("Run %s: resolved and snapshotted %d node specifications", run.id, len(registrations))
        cancellation = cancellation if cancellation is not None else Event()
        run.status = WorkflowRunStatus.RUNNING
        try:
            ordered = self._execution_order(snapshot)
            logger.debug("Run %s: execution order %s", run.id, [node.id for node in ordered])
            logger.info("Run %s: executing %d nodes sequentially", run.id, len(ordered))
            for node in ordered:
                if cancellation.is_set():
                    logger.debug("Run %s: cancellation requested before node %s", run.id, node.id)
                    raise CancelledError
                await self._execute_node(run, node, registrations[node.id], cancellation)
                if run.nodes[node.id].status == NodeRunStatus.FAILED:
                    run.status = WorkflowRunStatus.FAILED
                    run.error = run.nodes[node.id].error
                    self._finish_pending(run, failed_node=node.id)
                    return run
            run.status = WorkflowRunStatus.CANCELLED if cancellation.is_set() else WorkflowRunStatus.SUCCEEDED
        except CancelledError:
            logger.info("Run %s: cancelling workflow", run.id)
            run.status = WorkflowRunStatus.CANCELLED
            self._finish_pending(run)
            # Preserve asyncio task cancellation semantics for callers.
            task = current_task()
            if task is not None and task.cancelling():
                logger.debug("Run %s: propagating asyncio task cancellation", run.id)
                raise
        finally:
            run.finished_at = self._now()
            self._log_completion(run)
        return run

    def _log_completion(self, run: WorkflowRun) -> None:
        elapsed = (run.finished_at - run.started_at).total_seconds() if run.finished_at and run.started_at else 0.0
        logger.info("Run %s: workflow finished with status %s in %.3fs", run.id, run.status, elapsed)

    @staticmethod
    def _now() -> datetime:
        return datetime.now(UTC)

    def _resolve_nodes(self, workflow: Workflow) -> dict[UUID, NodeRegistration]:
        registrations: dict[UUID, NodeRegistration] = {}
        for node in workflow.nodes:
            registration = self._registry.resolve(node.type, node.version)
            registrations[node.id] = NodeRegistration(spec=registration.spec.model_copy(deep=True), executor=registration.executor)
        return registrations

    def _execution_order(self, workflow: Workflow) -> list[Node]:
        """Prefer workflow list order whenever multiple nodes are ready."""
        dependencies: dict[UUID, set[UUID]] = {node.id: set() for node in workflow.nodes}
        for edge in workflow.edges:
            dependencies[edge.target_node].add(edge.source_node)
        sorter = TopologicalSorter(dependencies)
        sorter.prepare()
        positions = {node.id: index for index, node in enumerate(workflow.nodes)}
        ready: list[tuple[int, UUID]] = []
        ordered: list[Node] = []
        while sorter.is_active():
            for node_id in sorter.get_ready():
                heappush(ready, (positions[node_id], node_id))
            index, node_id = heappop(ready)
            ordered.append(workflow.nodes[index])
            sorter.done(node_id)
        return ordered

    async def _execute_node(self, run: WorkflowRun, node: Node, registration: NodeRegistration, cancellation: Event) -> None:
        record = run.nodes[node.id]
        record.status = NodeRunStatus.RUNNING
        record.started_at = self._now()
        logger.info("Run %s: starting node %s (%s v%d)", run.id, node.id, node.type, node.version)
        context = NodeExecutionContext(run_id=run.id, node_id=node.id, logger=getLogger(f"mushin.run.{run.id}.{node.id}"), cancellation=cancellation)
        try:
            context.raise_if_cancelled()
            inputs = self._collect_inputs(run, node, registration.spec)
            logger.debug("Run %s node %s: collected input ports %s", run.id, node.id, list(inputs))
            inputs = self._validate_values(node.id, registration.spec.inputs, inputs)
            config = registration.spec.config_model.model_validate(node.config).model_dump(mode="json")
            logger.debug("Run %s node %s: inputs and configuration validated; invoking executor", run.id, node.id)
            outputs = await registration.executor.execute(MappingProxyType(inputs), MappingProxyType(config), context)
            logger.debug("Run %s node %s: executor returned; checking output contract", run.id, node.id)
            outputs = self._check_outputs(outputs, registration.spec)
            record.outputs = self._validate_values(node.id, registration.spec.outputs, outputs)
            record.status = NodeRunStatus.SUCCEEDED
        except CancelledError:
            record.status = NodeRunStatus.CANCELLED
            raise
        except PortValueError as exc:
            logger.debug("Run %s node %s: type validation failed for port %s", run.id, node.id, exc.port_name)
            record.status = NodeRunStatus.FAILED
            record.error = RunError(code="invalid_port_value", message=str(exc), node_id=exc.node_id, port_name=exc.port_name)
        except Exception as exc:  # noqa: BLE001 - Don't catch blind exception
            logger.debug("Run %s node %s: execution raised %s", run.id, node.id, type(exc).__name__)
            record.status = NodeRunStatus.FAILED
            record.error = RunError(code="node_execution_failed", message=f"{type(exc).__name__}: {exc}")
        finally:
            record.finished_at = self._now()
            logger.info("Run %s: node %s finished with status %s", run.id, node.id, record.status)

    def _collect_inputs(self, run: WorkflowRun, node: Node, spec: NodeSpec) -> dict[str, object]:
        inputs: dict[str, object] = {}
        for edge in run.workflow.edges:
            if edge.target_node == node.id:
                outputs = run.nodes[edge.source_node].outputs
                if edge.source_port in outputs:
                    inputs[edge.target_port] = outputs[edge.source_port]
        missing = [port.name for port in spec.inputs if port.required and port.name not in inputs]
        if missing:
            raise ValueError(f"Missing required inputs: {missing}")
        return inputs

    def _check_outputs(self, outputs: Mapping[str, object], spec: NodeSpec) -> dict[str, object]:
        if not isinstance(outputs, Mapping):
            raise TypeError("Executor outputs must be a mapping")
        declared = {port.name for port in spec.outputs}
        unexpected = [name for name in outputs if name not in declared]
        missing = [port.name for port in spec.outputs if port.required and port.name not in outputs]
        if unexpected or missing:
            raise ValueError(f"Invalid output ports; unexpected: {unexpected}, missing required: {missing}")
        return dict(outputs)

    def _validate_values(self, node_id: UUID, ports: list[Port], values: Mapping[str, object]) -> dict[str, object]:
        """Keep adapter-produced values, including structured model instances."""
        validated: dict[str, object] = {}
        for port in ports:
            if port.name not in values:
                continue
            try:
                validated[port.name] = self._type_registry.validate(port.type, values[port.name])
            except ValidationError as exc:
                messages = [error["msg"] for error in exc.errors(include_url=False, include_input=False)]
                raise PortValueError(node_id, port, "; ".join(messages)) from exc
            except KeyError as exc:
                raise PortValueError(node_id, port, f"Unknown value type {port.type!r}") from exc
        return validated

    def _finish_pending(self, run: WorkflowRun, *, failed_node: UUID | None = None) -> None:
        """Block all descendants of a failure and cancel other pending nodes."""
        blocked: set[UUID] = set()
        if failed_node is not None:
            children: dict[UUID, set[UUID]] = {}
            for edge in run.workflow.edges:
                children.setdefault(edge.source_node, set()).add(edge.target_node)
            remaining = list(children.get(failed_node, ()))
            while remaining:
                node_id = remaining.pop()
                if node_id not in blocked:
                    blocked.add(node_id)
                    remaining.extend(children.get(node_id, ()))
        for node_id, record in run.nodes.items():
            if record.status == NodeRunStatus.PENDING:
                record.status = NodeRunStatus.BLOCKED if node_id in blocked else NodeRunStatus.CANCELLED
                record.finished_at = self._now()
                logger.debug("Run %s node %s: unstarted node marked %s", run.id, node_id, record.status)
