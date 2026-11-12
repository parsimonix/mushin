from __future__ import annotations

import re
from typing import Self
from uuid import UUID, uuid4

from pydantic import BaseModel, ConfigDict, Field, model_validator

from mushin.models.node import Edge, Node, Workflow, WorkflowInput, WorkflowOutput


class NamedEdge(Edge):
    source_node: str = Field(min_length=1)
    target_node: str = Field(min_length=1)


class NamedInput(WorkflowInput):
    target_node: str = Field(min_length=1)


class NamedOutput(WorkflowOutput):
    source_node: str = Field(min_length=1)


class WorkflowDocument(BaseModel):
    """Authoring form with local node keys instead of UUID references.

    IDs are optional and generated when the document is parsed. Importing an
    ID-free file again creates a new workflow. Save the converted Workflow to
    retain those identities in the repository.
    """

    model_config = ConfigDict(extra="forbid")

    id: UUID = Field(default_factory=uuid4)
    name: str = Field(min_length=1)
    schema_version: int = Field(default=1, ge=1)
    nodes: dict[str, Node] = Field(default_factory=dict)
    edges: list[NamedEdge] = Field(default_factory=list)
    inputs: list[NamedInput] = Field(default_factory=list)
    outputs: list[NamedOutput] = Field(default_factory=list)

    @classmethod
    def from_workflow(cls, workflow: Workflow) -> Self:
        """Translate UUID references into deterministic, unique author keys."""
        keys: dict[UUID, str] = {}
        nodes: dict[str, Node] = {}
        for node in workflow.nodes:
            if node.id in keys:
                raise ValueError(f"Cannot export author form with duplicate node ID {node.id}")
            base = re.sub(r"\W+", "_", (node.name or node.type).strip()).strip("_").lower() or "node"
            key = base
            suffix = 2
            # Labels need not be unique, but author references must be.
            while key in nodes:
                key = f"{base}_{suffix}"
                suffix += 1
            keys[node.id] = key
            nodes[key] = node.model_copy(deep=True)

        def resolve(node_id: UUID) -> str:
            try:
                return keys[node_id]
            except KeyError:
                raise ValueError(f"Cannot export author form: reference to missing node {node_id}") from None

        return cls(
            id=workflow.id, name=workflow.name, schema_version=workflow.schema_version, nodes=nodes,
            edges=[NamedEdge(source_node=resolve(edge.source_node), source_port=edge.source_port,
                             target_node=resolve(edge.target_node), target_port=edge.target_port) for edge in workflow.edges],
            inputs=[NamedInput(name=binding.name, target_node=resolve(binding.target_node), target_port=binding.target_port)
                    for binding in workflow.inputs],
            outputs=[NamedOutput(name=binding.name, source_node=resolve(binding.source_node), source_port=binding.source_port)
                     for binding in workflow.outputs],
        )

    @model_validator(mode="after")
    def validate_references(self) -> Self:
        if any(not key.strip() for key in self.nodes):
            raise ValueError("Node keys must not be empty or whitespace")
        for index, edge in enumerate(self.edges):
            for reference in (edge.source_node, edge.target_node):
                if reference not in self.nodes:
                    raise ValueError(f"Edge {index} references unknown node {reference!r}")
        for direction, bindings in (("input", self.inputs), ("output", self.outputs)):
            for binding in bindings:
                reference = binding.target_node if direction == "input" else binding.source_node
                if reference not in self.nodes:
                    raise ValueError(f"Workflow {direction} {binding.name!r} references unknown node {reference!r}")
        return self

    def to_workflow(self) -> Workflow:
        """Resolve local keys, leaving graph/type validation to WorkflowValidator."""
        return Workflow(
            id=self.id, name=self.name, schema_version=self.schema_version,
            # Local keys become display names, but never replace persistent UUIDs.
            nodes=[node.model_copy(deep=True, update={"name": node.name or key}) for key, node in self.nodes.items()],
            edges=[Edge(
                source_node=self.nodes[edge.source_node].id, source_port=edge.source_port,
                target_node=self.nodes[edge.target_node].id, target_port=edge.target_port,
            ) for edge in self.edges],
            inputs=[WorkflowInput(
                name=binding.name, target_node=self.nodes[binding.target_node].id, target_port=binding.target_port,
            ) for binding in self.inputs],
            outputs=[WorkflowOutput(
                name=binding.name, source_node=self.nodes[binding.source_node].id, source_port=binding.source_port,
            ) for binding in self.outputs],
        )
