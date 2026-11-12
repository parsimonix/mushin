from __future__ import annotations

import asyncio
import unittest
from datetime import timedelta
from logging import getLogger
from typing import Annotated
from uuid import uuid4

from pydantic import AfterValidator, BaseModel, ConfigDict, TypeAdapter

from mushin.models.node import Edge, Node, NodeSpec, Port, Workflow
from mushin.models.registry import NodeRegistry, create_default_type_registry
from mushin.models.run import NodeExecutionContext, NodeRunStatus, WorkflowRunner, WorkflowRunStatus


class Config(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: int = 2


class Executor:
    def __init__(self, function):
        self.function = function

    async def execute(self, inputs, config, context):
        return await self.function(inputs, config, context)


class WorkflowRunnerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.registry = NodeRegistry()
        self.types = create_default_type_registry()
        self.runner = WorkflowRunner(self.registry, self.types)
        self.calls = []

    def register(self, name, function, inputs=(), *, optional_output=False, value_type="number"):
        spec = NodeSpec(
            type=name,
            config_model=Config,
            inputs=[Port(name=port, direction="input", type=value_type) for port in inputs],
            outputs=[Port(name="out", direction="output", type=value_type, required=not optional_output)],
        )
        self.registry.register(spec, Executor(function))
        return Node(id=uuid4(), type=name)

    async def constant(self, inputs, config, context):
        self.calls.append(context.node_id)
        return {"out": config["value"]}

    async def add(self, inputs, config, context):
        self.calls.append(context.node_id)
        return {"out": inputs["a"] + inputs["b"]}

    def workflow(self, nodes, edges=()):
        return Workflow(id=uuid4(), name="Test", nodes=nodes, edges=list(edges))

    def edge(self, source, target, port="a"):
        return Edge(source_node=source.id, source_port="out", target_node=target.id, target_port=port)

    async def test_dependency_order_fan_out_join_and_timestamps(self):
        source = self.register("constant", self.constant)
        target = self.register("add", self.add, ("a", "b"))
        workflow = self.workflow([target, source], [self.edge(source, target), self.edge(source, target, "b")])
        before = workflow.model_dump_json()
        run = await self.runner.run(workflow)
        self.assertEqual(run.status, WorkflowRunStatus.SUCCEEDED)
        self.assertEqual(self.calls, [source.id, target.id])
        self.assertEqual(run.nodes[target.id].outputs, {"out": 4})
        self.assertEqual(workflow.model_dump_json(), before)
        self.assertIsNot(run.workflow, workflow)
        for record in [run, *run.nodes.values()]:
            self.assertLessEqual(record.started_at, record.finished_at)
            self.assertEqual(record.started_at.utcoffset(), timedelta(0))

    async def test_stable_ready_order_and_empty_graph(self):
        first = self.register("first", self.constant)
        second = self.register("second", self.constant)
        await self.runner.run(self.workflow([second, first]))
        self.assertEqual(self.calls, [second.id, first.id])
        self.assertEqual((await self.runner.run(self.workflow([]))).status, WorkflowRunStatus.SUCCEEDED)

    async def test_invalid_graph_never_executes(self):
        target = self.register("add", self.add, ("a", "b"))
        run = await self.runner.run(self.workflow([target]))
        self.assertEqual(run.status, WorkflowRunStatus.FAILED)
        self.assertEqual(run.error.code, "invalid_workflow")
        self.assertEqual(len(run.validation_issues), 2)
        self.assertEqual(self.calls, [])

    async def test_failure_blocks_transitive_dependents(self):
        async def fail(*args):
            raise RuntimeError("broken")

        root = self.register("fail", fail)
        child = self.register("child", self.constant, ("a",))
        grandchild = self.register("grandchild", self.constant, ("a",))
        independent = self.register("independent", self.constant)
        run = await self.runner.run(self.workflow([root, child, grandchild, independent], [self.edge(root, child), self.edge(child, grandchild)]))
        self.assertEqual(run.status, WorkflowRunStatus.FAILED)
        self.assertEqual(run.nodes[root.id].status, NodeRunStatus.FAILED)
        self.assertEqual(run.nodes[child.id].status, NodeRunStatus.BLOCKED)
        self.assertEqual(run.nodes[grandchild.id].status, NodeRunStatus.BLOCKED)
        self.assertEqual(run.nodes[independent.id].status, NodeRunStatus.CANCELLED)
        self.assertIn("broken", run.error.message)
        self.assertEqual(self.calls, [])

    async def test_output_contract_errors(self):
        for output in ({}, {"out": 2, "extra": 3}, None, [2]):
            with self.subTest(output=output):
                async def bad(*args):
                    return output

                node = self.register(f"bad{len(self.registry.list_specs())}", bad)
                run = await self.runner.run(self.workflow([node]))
                self.assertEqual(run.status, WorkflowRunStatus.FAILED)
                self.assertEqual(run.nodes[node.id].outputs, {})

    async def test_missing_optional_output_fails_required_consumer(self):
        async def omitted(*args):
            return {}

        source = self.register("optional", omitted, optional_output=True)
        target = self.register("target", self.constant, ("a",))
        run = await self.runner.run(self.workflow([source, target], [self.edge(source, target)]))
        self.assertEqual(run.nodes[source.id].status, NodeRunStatus.SUCCEEDED)
        self.assertEqual(run.nodes[target.id].status, NodeRunStatus.FAILED)
        self.assertIn("Missing required inputs", run.error.message)

    async def test_explicit_null_is_present(self):
        async def null(*args):
            return {"out": None}

        self.types.register("nullable_number", TypeAdapter(int | float | None))
        node = self.register("null", null, value_type="nullable_number")
        self.assertEqual((await self.runner.run(self.workflow([node]))).status, WorkflowRunStatus.SUCCEEDED)

    async def test_invalid_output_type_has_node_and_port_and_blocks_consumer(self):
        async def invalid(*args):
            return {"out": "not a number"}

        source = self.register("invalid", invalid)
        target = self.register("consumer", self.constant, ("a",))
        run = await self.runner.run(self.workflow([source, target], [self.edge(source, target)]))
        self.assertEqual(run.status, WorkflowRunStatus.FAILED)
        self.assertEqual(run.error.code, "invalid_port_value")
        self.assertEqual(run.error.node_id, source.id)
        self.assertEqual(run.error.port_name, "out")
        self.assertIn(str(source.id), run.error.message)
        self.assertIn("output port 'out'", run.error.message)
        self.assertEqual(run.nodes[source.id].outputs, {})
        self.assertEqual(run.nodes[target.id].status, NodeRunStatus.BLOCKED)
        self.assertEqual(self.calls, [])

    async def test_structured_values_are_validated_on_inputs_and_outputs(self):
        class Metadata(BaseModel):
            model_config = ConfigDict(revalidate_instances="always")
            title: str

        self.types.register("metadata", TypeAdapter(Metadata))

        async def produce(*args):
            return {"out": {"title": "Example"}}

        async def consume(inputs, config, context):
            self.assertIsInstance(inputs["a"], Metadata)
            return {"out": inputs["a"]}

        source = self.register("metadata_source", produce, value_type="metadata")
        target = self.register("metadata_consumer", consume, ("a",), value_type="metadata")
        run = await self.runner.run(self.workflow([source, target], [self.edge(source, target)]))
        self.assertEqual(run.status, WorkflowRunStatus.SUCCEEDED)
        self.assertEqual(run.nodes[target.id].outputs["out"].title, "Example")

    async def test_invalid_shared_input_is_rejected_before_executor(self):
        class Metadata(BaseModel):
            model_config = ConfigDict(revalidate_instances="always")
            title: str

        validated_values = []

        def capture(value):
            validated_values.append(value)
            return value

        self.types.register("metadata", TypeAdapter(Annotated[Metadata, AfterValidator(capture)]))

        async def produce(*args):
            return {"out": {"title": "Example"}}

        async def corrupt(*args):
            # Simulate external mutation of an already validated mutable value.
            validated_values[0].title = 123
            return {"out": 1}

        source = self.register("metadata_source", produce, value_type="metadata")
        mutator = self.register("mutator", corrupt)
        target = self.register("consumer", self.constant, ("a",), value_type="metadata")
        run = await self.runner.run(self.workflow([source, mutator, target], [self.edge(source, target)]))
        self.assertEqual(run.status, WorkflowRunStatus.FAILED)
        self.assertEqual(run.error.node_id, target.id)
        self.assertEqual(run.error.port_name, "a")
        self.assertIn("input port 'a'", run.error.message)
        self.assertEqual(self.calls, [])

    async def test_unknown_port_type_prevents_execution(self):
        node = self.register("unknown", self.constant, value_type="unregistered")
        run = await self.runner.run(self.workflow([node]))
        self.assertEqual(run.error.code, "invalid_workflow")
        self.assertEqual(run.validation_issues[0].code, "unknown_port_type")
        self.assertEqual(self.calls, [])

    async def test_cancellation_before_and_between_nodes(self):
        event = asyncio.Event()
        event.set()
        first = self.register("first", self.constant)
        run = await self.runner.run(self.workflow([first]), cancellation=event)
        self.assertEqual(run.status, WorkflowRunStatus.CANCELLED)
        self.assertIsNone(run.nodes[first.id].started_at)

        async def stop(*args):
            event.set()
            return {"out": 1}

        event.clear()
        stopping = self.register("stop", stop)
        run = await self.runner.run(self.workflow([stopping, first]), cancellation=event)
        self.assertEqual(run.nodes[stopping.id].status, NodeRunStatus.SUCCEEDED)
        self.assertEqual(run.nodes[first.id].status, NodeRunStatus.CANCELLED)
        self.assertEqual(self.calls, [])

    async def test_cooperative_cancellation_inside_executor(self):
        async def stop(inputs, config, context):
            context.cancellation.set()
            context.raise_if_cancelled()

        node = self.register("stop", stop)
        run = await self.runner.run(self.workflow([node]))
        self.assertEqual(run.status, WorkflowRunStatus.CANCELLED)
        self.assertEqual(run.nodes[node.id].status, NodeRunStatus.CANCELLED)
        self.assertIsNotNone(run.nodes[node.id].finished_at)

    async def test_external_task_cancellation_propagates(self):
        started = asyncio.Event()
        cleaned_up = asyncio.Event()

        async def wait(*args):
            started.set()
            try:
                await asyncio.Event().wait()
            finally:
                cleaned_up.set()

        node = self.register("wait", wait)
        task = asyncio.create_task(self.runner.run(self.workflow([node])))
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cleaned_up.is_set())

    async def test_snapshot_and_config_defaults(self):
        async def edit_original(inputs, config, context):
            workflow.name = "Changed"
            other.config["value"] = 99
            return {"out": config["value"]}

        node = self.register("edit", edit_original)
        other = self.register("other", self.constant)
        workflow = self.workflow([node, other])
        run = await self.runner.run(workflow)
        self.assertEqual(run.workflow.name, "Test")
        self.assertEqual(run.nodes[other.id].outputs["out"], 2)

    async def test_context_cancellation_check(self):
        context = NodeExecutionContext(run_id=uuid4(), node_id=uuid4(), logger=getLogger("test"))
        context.raise_if_cancelled()
        context.cancellation.set()
        with self.assertRaises(asyncio.CancelledError):
            context.raise_if_cancelled()


if __name__ == "__main__":
    unittest.main()
