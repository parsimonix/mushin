from __future__ import annotations

from collections import Counter
from graphlib import CycleError, TopologicalSorter
from uuid import UUID

from pydantic import BaseModel, Field, ValidationError

from mushin.models.node import Edge, Node, NodeSpec, Port, Workflow
from mushin.models.registry import NodeRegistry, TypeRegistry, create_default_type_registry


class ValidationIssue(BaseModel):
    """A diagnostic that a client can associate with part of a workflow."""

    code: str = Field(min_length=1, description="Stable, machine-readable error code.")
    message: str = Field(min_length=1)
    node_id: UUID | None = None
    port_name: str | None = None
    edge_index: int | None = Field(default=None, ge=0, description="Index in the workflow's edges list.")
    binding_name: str | None = None
    binding_direction: str | None = None
    binding_index: int | None = Field(default=None, ge=0)


class WorkflowValidator:
    """Check whether a workflow can run using the supplied node registry."""

    def __init__(self, registry: NodeRegistry, type_registry: TypeRegistry | None = None) -> None:
        self._registry = registry
        self._type_registry = type_registry if type_registry is not None else create_default_type_registry()

    def validate(self, workflow: Workflow) -> list[ValidationIssue]:
        """Return all detected issues, or an empty list for a valid workflow.

        Check duplicate node IDs, unknown node types/versions, configuration,
        edge endpoints, port types, multiple connections to an input, missing
        required inputs, workflow bindings, and cycles. Required inputs need
        an incoming edge or an exposed workflow input.
        """
        specs, issues = self._validate_nodes(workflow)
        issues.extend(self._validate_declared_types(specs))
        inputs = {node_id: {port.name: port for port in spec.inputs} for node_id, spec in specs.items()}
        outputs = {node_id: {port.name: port for port in spec.outputs} for node_id, spec in specs.items()}
        incoming, edge_issues = self._validate_edges(workflow, inputs, outputs)
        issues.extend(edge_issues)
        issues.extend(self._validate_bindings(workflow, inputs, outputs, incoming))
        issues.extend(self._validate_required_inputs(inputs, incoming))
        issues.extend(self._validate_cycles(workflow))
        return issues

    def _validate_nodes(self, workflow: Workflow) -> tuple[dict[UUID, NodeSpec], list[ValidationIssue]]:
        """Resolve unambiguous specs and validate each node's configuration."""
        counts = Counter(node.id for node in workflow.nodes)
        issues = [
            ValidationIssue(code="duplicate_node_id", message="Node ID appears more than once", node_id=node_id)
            for node_id, count in counts.items()
            if count > 1
        ]
        specs: dict[UUID, NodeSpec] = {}
        for node in workflow.nodes:
            try:
                spec = self._registry.resolve(node.type, node.version).spec
            except KeyError:
                issues.append(ValidationIssue(code="unknown_node_type", message=f"Unknown node type {node.type!r} version {node.version}", node_id=node.id))
                continue
            # Duplicate IDs could refer to different node types; avoid deriving
            # misleading port diagnostics from either definition.
            if counts[node.id] == 1:
                specs[node.id] = spec
            issues.extend(self._validate_config(node, spec))
        return specs, issues

    def _validate_declared_types(self, specs: dict[UUID, NodeSpec]) -> list[ValidationIssue]:
        """Check all declared ports, including those without connections."""
        issues: list[ValidationIssue] = []
        for node_id, spec in specs.items():
            for port in (*spec.inputs, *spec.outputs):
                try:
                    self._type_registry.resolve(port.type)
                    if port.default is not None:
                        self._type_registry.validate(port.type, port.default.value)
                except ValidationError as exc:
                    messages = "; ".join(error["msg"] for error in exc.errors(include_url=False, include_input=False))
                    issues.append(
                        ValidationIssue(
                            code="invalid_port_default",
                            message=f"Invalid default for port {port.name!r}: {messages}",
                            node_id=node_id,
                            port_name=port.name,
                        )
                    )
                except KeyError:
                    issues.append(
                        ValidationIssue(
                            code="unknown_port_type",
                            message=f"Unknown type {port.type!r} on {port.direction} port {port.name!r}",
                            node_id=node_id,
                            port_name=port.name,
                        )
                    )
        return issues

    def _validate_config(self, node: Node, spec: NodeSpec) -> list[ValidationIssue]:
        issues: list[ValidationIssue] = []
        try:
            spec.config_model.model_validate(node.config)
        except ValidationError as exc:
            for error in exc.errors(include_url=False, include_input=False):
                location = ".".join(str(part) for part in error["loc"]) or "configuration"
                issues.append(ValidationIssue(code="invalid_config", message=f"{location}: {error['msg']}", node_id=node.id))
        return issues

    def _validate_edges(
        self,
        workflow: Workflow,
        inputs: dict[UUID, dict[str, Port]],
        outputs: dict[UUID, dict[str, Port]],
    ) -> tuple[Counter[tuple[UUID, str]], list[ValidationIssue]]:
        """Check endpoints and types, and count connections to declared inputs."""
        node_ids = {node.id for node in workflow.nodes}
        incoming: Counter[tuple[UUID, str]] = Counter()
        issues: list[ValidationIssue] = []
        for index, edge in enumerate(workflow.edges):
            endpoint_issues = self._validate_endpoint(edge.source_node, edge.source_port, "output", index, node_ids, outputs)
            endpoint_issues.extend(self._validate_endpoint(edge.target_node, edge.target_port, "input", index, node_ids, inputs))
            # Report missing nodes before port errors, retaining source/target order.
            issues.extend(sorted(endpoint_issues, key=lambda issue: issue.code != "missing_node"))
            source = outputs.get(edge.source_node, {}).get(edge.source_port)
            target = inputs.get(edge.target_node, {}).get(edge.target_port)
            if target is not None:
                # Count even mistyped edges: they still occupy the input and
                # should not also produce a spurious "missing input" issue.
                key = (edge.target_node, edge.target_port)
                incoming[key] += 1
                if incoming[key] > 1:
                    issues.append(
                        ValidationIssue(
                            code="multiple_input_connections",
                            message="Input accepts at most one incoming edge",
                            node_id=edge.target_node,
                            port_name=edge.target_port,
                            edge_index=index,
                        )
                    )
            issues.extend(self._validate_port_types(edge, index, source, target))
        return incoming, issues

    def _validate_endpoint(
        self,
        node_id: UUID,
        port_name: str,
        direction: str,
        edge_index: int,
        node_ids: set[UUID],
        ports: dict[UUID, dict[str, Port]],
    ) -> list[ValidationIssue]:
        if node_id not in node_ids:
            code, message = "missing_node", "Edge references an unknown node"
        elif node_id in ports and port_name not in ports[node_id]:
            code, message = "missing_port", f"Unknown {direction} port {port_name!r}"
        else:
            # Unknown or ambiguous node definitions cannot supply port diagnostics.
            return []
        return [ValidationIssue(code=code, message=message, node_id=node_id, port_name=port_name, edge_index=edge_index)]

    def _validate_port_types(self, edge: Edge, index: int, source: Port | None, target: Port | None) -> list[ValidationIssue]:
        # Graph compatibility uses stable type IDs, not implicit Python coercion;
        # conversions belong in explicit adapter nodes.
        if source is None or target is None or source.type == target.type:
            return []
        return [
            ValidationIssue(
                code="incompatible_port_types",
                message=f"Output type {source.type!r} does not match input type {target.type!r}",
                node_id=edge.target_node,
                port_name=edge.target_port,
                edge_index=index,
            )
        ]

    def _validate_bindings(
        self,
        workflow: Workflow,
        inputs: dict[UUID, dict[str, Port]],
        outputs: dict[UUID, dict[str, Port]],
        incoming: Counter[tuple[UUID, str]],
    ) -> list[ValidationIssue]:
        """Validate public names and endpoints; count input bindings as providers.

        Binding types are inherited from their referenced ports. Input and
        output names occupy separate namespaces.
        """
        node_ids = {node.id for node in workflow.nodes}
        issues: list[ValidationIssue] = []
        for direction, bindings, ports in (("input", workflow.inputs, inputs), ("output", workflow.outputs, outputs)):
            names: set[str] = set()
            for index, binding in enumerate(bindings):
                if direction == "input":
                    node_id, port_name = binding.target_node, binding.target_port
                else:
                    node_id, port_name = binding.source_node, binding.source_port
                location = {
                    "node_id": node_id,
                    "port_name": port_name,
                    "binding_name": binding.name,
                    "binding_direction": direction,
                    "binding_index": index,
                }
                if binding.name in names:
                    issues.append(
                        ValidationIssue(
                            code="duplicate_binding_name",
                            message=f"Duplicate workflow {direction} name {binding.name!r}",
                            **location,
                        )
                    )
                names.add(binding.name)
                if node_id not in node_ids:
                    issues.append(ValidationIssue(code="missing_node", message="Workflow binding references an unknown node", **location))
                elif node_id in ports and port_name not in ports[node_id]:
                    issues.append(ValidationIssue(code="missing_port", message=f"Unknown {direction} port {port_name!r}", **location))
                elif direction == "input" and port_name in ports.get(node_id, {}):
                    # A public binding supplies the same slot as an incoming edge.
                    key = (node_id, port_name)
                    if incoming[key]:
                        issues.append(
                            ValidationIssue(
                                code="conflicting_input_binding",
                                message="Input already has an incoming edge or workflow binding",
                                **location,
                            )
                        )
                    incoming[key] += 1
        return issues

    def _validate_required_inputs(self, inputs: dict[UUID, dict[str, Port]], incoming: Counter[tuple[UUID, str]]) -> list[ValidationIssue]:
        return [
            ValidationIssue(
                code="missing_required_input", message="Required input has no incoming edge or workflow binding", node_id=node_id, port_name=port.name
            )
            for node_id, ports in inputs.items()
            for port in ports.values()
            if port.required and port.default is None and not incoming[(node_id, port.name)]
        ]

    def _validate_cycles(self, workflow: Workflow) -> list[ValidationIssue]:
        # Check node-level cycles even when port or type checks fail.
        dependencies: dict[UUID, set[UUID]] = {node.id: set() for node in workflow.nodes}
        for edge in workflow.edges:
            if edge.source_node in dependencies and edge.target_node in dependencies:
                dependencies[edge.target_node].add(edge.source_node)
        try:
            TopologicalSorter(dependencies).prepare()
        except CycleError as exc:
            cycle = exc.args[1]
            return [ValidationIssue(code="cycle", message=f"Workflow contains a cycle: {' -> '.join(str(node_id) for node_id in cycle)}", node_id=cycle[0])]
        return []
