from __future__ import annotations

import unittest
from uuid import uuid4

from pydantic import BaseModel, ConfigDict, TypeAdapter

from mushin.models.node import Edge, Node, NodeSpec, Port, Workflow, WorkflowInput, WorkflowOutput
from mushin.models.registry import NodeRegistry, TypeRegistry
from mushin.models.validation import WorkflowValidator


class EmptyConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")


class RequiredConfig(EmptyConfig):
    value: int


class UnusedExecutor:
    async def execute(self, inputs, config, context):
        raise AssertionError("Validation must not execute nodes")


class WorkflowValidatorTests(unittest.TestCase):
    def setUp(self):
        self.registry = NodeRegistry()
        for spec in (
            NodeSpec(type="source", outputs=[Port(name="out", direction="output", type="number")], config_model=EmptyConfig),
            NodeSpec(
                type="step",
                inputs=[Port(name="in", direction="input", type="number")],
                outputs=[Port(name="out", direction="output", type="number")],
                config_model=EmptyConfig,
            ),
            NodeSpec(type="text", inputs=[Port(name="in", direction="input", type="string")], config_model=EmptyConfig),
            NodeSpec(type="optional", inputs=[Port(name="in", direction="input", type="number", required=False)], config_model=EmptyConfig),
            NodeSpec(type="configured", config_model=RequiredConfig),
        ):
            self.registry.register(spec, UnusedExecutor())
        self.validator = WorkflowValidator(self.registry)
        self.source = Node(id=uuid4(), type="source")
        self.target = Node(id=uuid4(), type="step")

    def edge(self, source=None, target=None, **kwargs):
        return Edge(
            source_node=(source or self.source).id,
            source_port=kwargs.get("source_port", "out"),
            target_node=(target or self.target).id,
            target_port=kwargs.get("target_port", "in"),
        )

    def validate(self, nodes, edges=()):
        workflow = Workflow(id=uuid4(), name="Test", nodes=nodes, edges=list(edges))
        before = workflow.model_dump_json()
        issues = self.validator.validate(workflow)
        self.assertEqual(workflow.model_dump_json(), before)
        return issues

    def codes(self, nodes, edges=()):
        return [issue.code for issue in self.validate(nodes, edges)]

    def test_valid_empty_and_optional_graphs(self):
        self.assertEqual(self.validate([]), [])
        self.assertEqual(self.validate([self.source, self.target], [self.edge()]), [])
        self.assertEqual(self.validate([Node(id=uuid4(), type="optional")]), [])

    def test_fan_out_and_repeated_validation(self):
        other = Node(id=uuid4(), type="step")
        for _ in range(2):
            self.assertEqual(self.validate([self.source, self.target, other], [self.edge(), self.edge(target=other)]), [])

    def test_duplicate_ids_do_not_produce_ambiguous_port_errors(self):
        duplicate = Node(id=self.source.id, type="step")
        codes = self.codes([self.source, duplicate, self.target], [self.edge()])
        self.assertEqual(codes, ["duplicate_node_id"])

    def test_unknown_types_and_versions(self):
        codes = self.codes([Node(id=uuid4(), type="unknown"), Node(id=uuid4(), type="source", version=2)])
        self.assertEqual(codes, ["unknown_node_type", "unknown_node_type"])

    def test_configuration_errors_are_collected(self):
        nodes = [Node(id=uuid4(), type="configured", config={"extra": True}), self.target]
        issues = self.validate(nodes)
        self.assertEqual([issue.code for issue in issues], ["invalid_config", "invalid_config", "missing_required_input"])
        self.assertEqual(issues[0].node_id, nodes[0].id)

    def test_missing_nodes_and_ports(self):
        missing = Node(id=uuid4(), type="source")
        issues = self.validate([self.target], [self.edge(source=missing)])
        self.assertEqual([issue.code for issue in issues], ["missing_node"])
        self.assertEqual(issues[0].edge_index, 0)
        codes = self.codes([self.source, self.target], [self.edge(source_port="in", target_port="out")])
        self.assertEqual(codes, ["missing_port", "missing_port", "missing_required_input"])
        self.assertEqual(self.codes([self.source], [self.edge()]), ["missing_node"])

    def test_incompatible_types_and_multiple_connections(self):
        target = Node(id=uuid4(), type="text")
        issues = self.validate([self.source, target], [self.edge(target=target)])
        self.assertEqual([issue.code for issue in issues], ["incompatible_port_types"])
        self.assertEqual(issues[0].port_name, "in")
        issues = self.validate([self.source, self.target], [self.edge(), self.edge()])
        self.assertEqual([issue.code for issue in issues], ["multiple_input_connections"])
        self.assertEqual(issues[0].edge_index, 1)

    def test_required_input(self):
        issues = self.validate([self.target])
        self.assertEqual([issue.code for issue in issues], ["missing_required_input"])
        self.assertEqual(issues[0].node_id, self.target.id)

    def binding_workflow(self):
        return Workflow(
            id=uuid4(), name="Exposed step", nodes=[self.target],
            inputs=[WorkflowInput(name="value", target_node=self.target.id, target_port="in")],
            outputs=[WorkflowOutput(name="value", source_node=self.target.id, source_port="out")],
        )

    def test_bindings_satisfy_inputs_and_round_trip(self):
        workflow = self.binding_workflow()
        serialized = workflow.model_dump_json()
        restored = Workflow.model_validate_json(serialized)
        self.assertEqual(restored, workflow)
        self.assertEqual(self.validator.validate(restored), [])
        self.assertEqual(workflow.model_dump_json(), serialized)

    def test_binding_endpoints_and_diagnostic_locations(self):
        for direction in ("input", "output"):
            for missing_node in (False, True):
                with self.subTest(direction=direction, missing_node=missing_node):
                    workflow = self.binding_workflow()
                    binding = workflow.inputs[0] if direction == "input" else workflow.outputs[0]
                    if direction == "input":
                        binding.target_node = uuid4() if missing_node else self.target.id
                        binding.target_port = "in" if missing_node else "out"
                    else:
                        binding.source_node = uuid4() if missing_node else self.target.id
                        binding.source_port = "out" if missing_node else "in"
                    issue = self.validator.validate(workflow)[0]
                    self.assertEqual(issue.code, "missing_node" if missing_node else "missing_port")
                    self.assertEqual(issue.binding_name, "value")
                    self.assertEqual(issue.binding_direction, direction)
                    self.assertEqual(issue.binding_index, 0)
                    self.assertIsNone(issue.edge_index)

    def test_duplicate_binding_names_in_each_direction(self):
        workflow = self.binding_workflow()
        workflow.inputs.append(workflow.inputs[0].model_copy())
        workflow.outputs.append(workflow.outputs[0].model_copy())
        self.assertEqual(
            [issue.code for issue in self.validator.validate(workflow)],
            ["duplicate_binding_name", "conflicting_input_binding", "duplicate_binding_name"],
        )

    def test_binding_conflicts_with_edge_or_other_binding(self):
        workflow = self.binding_workflow()
        workflow.nodes.append(self.source)
        workflow.edges.append(self.edge())
        self.assertEqual([issue.code for issue in self.validator.validate(workflow)], ["conflicting_input_binding"])
        workflow.edges.clear()
        workflow.inputs.append(WorkflowInput(name="other", target_node=self.target.id, target_port="in"))
        self.assertEqual([issue.code for issue in self.validator.validate(workflow)], ["conflicting_input_binding"])

    def test_output_can_be_exposed_multiple_times_and_feed_edge(self):
        workflow = self.binding_workflow()
        other = Node(id=uuid4(), type="step")
        workflow.nodes.append(other)
        workflow.edges.append(self.edge(source=self.target, target=other))
        workflow.outputs.append(WorkflowOutput(name="alias", source_node=self.target.id, source_port="out"))
        self.assertEqual(self.validator.validate(workflow), [])

    def test_unresolved_binding_node_does_not_produce_port_errors(self):
        workflow = self.binding_workflow()
        workflow.nodes[0].type = "unknown"
        self.assertEqual([issue.code for issue in self.validator.validate(workflow)], ["unknown_node_type"])

    def test_unknown_types_on_unconnected_ports(self):
        self.registry.register(
            NodeSpec(
                type="custom",
                inputs=[Port(name="in", direction="input", type="custom", required=False)],
                outputs=[Port(name="out", direction="output", type="custom")],
                config_model=EmptyConfig,
            ),
            UnusedExecutor(),
        )
        node = Node(id=uuid4(), type="custom")
        issues = self.validate([node])
        self.assertEqual([issue.code for issue in issues], ["unknown_port_type", "unknown_port_type"])
        self.assertEqual([issue.port_name for issue in issues], ["in", "out"])
        self.assertTrue(all(issue.node_id == node.id for issue in issues))
        types = TypeRegistry()
        types.register("custom", TypeAdapter(str))
        validator = WorkflowValidator(self.registry, types)
        self.assertEqual(validator.validate(Workflow(id=uuid4(), name="Custom", nodes=[node])), [])

    def test_explicit_empty_type_registry_does_not_fall_back_to_defaults(self):
        validator = WorkflowValidator(self.registry, TypeRegistry())
        issues = validator.validate(Workflow(id=uuid4(), name="Empty types", nodes=[self.source]))
        self.assertEqual([issue.code for issue in issues], ["unknown_port_type"])

    def test_cycles_and_self_loops(self):
        other = Node(id=uuid4(), type="step")
        self.assertEqual(
            self.codes([self.target, other], [self.edge(self.target, other), self.edge(other, self.target)]),
            ["cycle"],
        )
        self.assertEqual(self.codes([self.target], [self.edge(self.target, self.target)]), ["cycle"])


if __name__ == "__main__":
    unittest.main()
