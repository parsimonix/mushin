from __future__ import annotations

import asyncio
import unittest
from datetime import timedelta
from logging import getLogger
from typing import Annotated
from uuid import uuid4

from pydantic import AfterValidator, BaseModel, ConfigDict, TypeAdapter

from mushin.models.node import Edge, Node, NodeSpec, Port, PortDefault, Workflow, WorkflowInput, WorkflowOutput
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

    def assert_event_states(self, events, run_states, node_states):
        self.assertEqual([event.status.value for event in events if event.type == "run_state_changed"], run_states)
        self.assertEqual(events[0].type, "run_state_changed")
        self.assertEqual(events[-1].type, "run_state_changed")
        self.assertEqual({event.node_id for event in events if event.type == "node_state_changed"}, set(node_states))
        for node_id, states in node_states.items():
            self.assertEqual(
                [event.status.value for event in events if event.type == "node_state_changed" and event.node_id == node_id],
                states,
            )
        self.assertEqual(len({event.run_id for event in events}), 1)
        self.assertTrue(all(event.timestamp.utcoffset() == timedelta(0) for event in events))

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

    def exposed_add(self):
        node = self.register("add", self.add, ("a", "b"))
        workflow = self.workflow([node])
        workflow.inputs = [
            WorkflowInput(name="left", target_node=node.id, target_port="a"),
            WorkflowInput(name="right", target_node=node.id, target_port="b"),
        ]
        workflow.outputs = [WorkflowOutput(name="sum", source_node=node.id, source_port="out")]
        return workflow

    async def test_workflow_inputs_and_outputs(self):
        workflow = self.exposed_add()
        supplied = {"left": 2, "right": 3}
        run = await self.runner.run(workflow, inputs=supplied)
        self.assertEqual(run.status, WorkflowRunStatus.SUCCEEDED)
        self.assertEqual(run.inputs, supplied)
        self.assertEqual(run.outputs, {"sum": 5})
        supplied["left"] = 99
        self.assertEqual(run.inputs["left"], 2)

    async def test_defaults_for_unconnected_and_exposed_inputs(self):
        workflow = self.exposed_add()
        node = workflow.nodes[0]
        spec = self.registry.resolve(node.type, node.version).spec
        spec.inputs[0].default = PortDefault(value=10)
        spec.inputs[1].default = PortDefault(value=20)
        for bindings in (list(workflow.inputs), []):
            workflow.inputs = bindings
            run = await self.runner.run(workflow)
            self.assertEqual(run.status, WorkflowRunStatus.SUCCEEDED)
            self.assertEqual(run.outputs, {"sum": 30})

    async def test_supplied_values_override_defaults(self):
        workflow = self.exposed_add()
        spec = self.registry.resolve("add", 1).spec
        spec.inputs[0].default = PortDefault(value=10)
        run = await self.runner.run(workflow, inputs={"left": 0, "right": 3})
        self.assertEqual(run.outputs, {"sum": 3})
        run = await self.runner.run(workflow, inputs={"left": None, "right": 3})
        self.assertEqual(run.error.code, "invalid_workflow_input")
        workflow.inputs = workflow.inputs[1:]
        source = self.register("constant", self.constant)
        workflow.nodes.append(source)
        workflow.edges.append(self.edge(source, workflow.nodes[0]))
        run = await self.runner.run(workflow, inputs={"right": 3})
        self.assertEqual(run.outputs, {"sum": 5})

    async def test_invalid_default_prevents_all_execution(self):
        workflow = self.exposed_add()
        self.registry.resolve("add", 1).spec.inputs[0].default = PortDefault(value="bad")
        run = await self.runner.run(workflow, inputs={"left": 2, "right": 3})
        self.assertEqual(run.error.code, "invalid_workflow")
        self.assertEqual(run.validation_issues[0].code, "invalid_port_default")
        self.assertEqual(self.calls, [])

    async def test_null_default_and_port_round_trip(self):
        self.types.register("nullable", TypeAdapter(int | None))

        async def echo(inputs, config, context):
            return {"out": inputs["a"]}

        node = self.register("echo", echo, ("a",), value_type="nullable")
        port = self.registry.resolve("echo", 1).spec.inputs[0]
        port.default = PortDefault(value=None)
        restored = Port.model_validate_json(port.model_dump_json())
        self.assertIsNotNone(restored.default)
        self.assertIsNone(restored.default.value)
        run = await self.runner.run(self.workflow([node]))
        self.assertEqual(run.status, WorkflowRunStatus.SUCCEEDED)
        self.assertEqual(run.nodes[node.id].outputs, {"out": None})
        with self.assertRaises(ValueError):
            Port(name="out", direction="output", type="nullable", default=PortDefault(value=None))

    async def test_invalid_workflow_inputs_execute_no_nodes(self):
        workflow = self.exposed_add()
        for supplied, code in (
            ({"left": 2}, "missing_workflow_input"),
            ({"left": 2, "right": 3, "extra": 4}, "unexpected_workflow_input"),
            ({"left": "bad", "right": 3}, "invalid_workflow_input"),
            ({"left": None, "right": 3}, "invalid_workflow_input"),
        ):
            with self.subTest(code=code, supplied=supplied):
                events = []
                run = await self.runner.run(workflow, inputs=supplied, on_event=events.append)
                self.assertEqual(run.status, WorkflowRunStatus.FAILED)
                self.assertEqual(run.error.code, code)
                self.assertEqual(run.outputs, {})
                self.assertEqual(self.calls, [])
                self.assertTrue(all(record.started_at is None for record in run.nodes.values()))
                self.assert_event_states(events, ["pending", "failed"], {workflow.nodes[0].id: ["pending", "cancelled"]})
                self.assertEqual(events[-1].error, run.error)

    async def test_optional_workflow_input_and_output_can_be_omitted(self):
        async def omitted(inputs, config, context):
            self.assertEqual(dict(inputs), {})
            return {}

        node = self.register("optional", omitted, optional_output=True)
        self.registry.resolve(node.type, node.version).spec.inputs.append(
            Port(name="a", direction="input", type="number", required=False)
        )
        workflow = self.workflow([node])
        workflow.inputs = [WorkflowInput(name="value", target_node=node.id, target_port="a")]
        workflow.outputs = [WorkflowOutput(name="value", source_node=node.id, source_port="out")]
        run = await self.runner.run(workflow)
        self.assertEqual(run.status, WorkflowRunStatus.SUCCEEDED)
        self.assertEqual(run.outputs, {})

    async def test_nullable_workflow_input_is_present(self):
        self.types.register("nullable", TypeAdapter(int | None))

        async def echo(inputs, config, context):
            return {"out": inputs["a"]}

        node = self.register("echo", echo, ("a",), value_type="nullable")
        workflow = self.workflow([node])
        workflow.inputs = [WorkflowInput(name="value", target_node=node.id, target_port="a")]
        workflow.outputs = [WorkflowOutput(name="value", source_node=node.id, source_port="out")]
        run = await self.runner.run(workflow, inputs={"value": None})
        self.assertEqual(run.status, WorkflowRunStatus.SUCCEEDED)
        self.assertEqual(run.outputs, {"value": None})

    async def test_stable_ready_order_and_empty_graph(self):
        first = self.register("first", self.constant)
        second = self.register("second", self.constant)
        await self.runner.run(self.workflow([second, first]))
        self.assertEqual(self.calls, [second.id, first.id])
        self.assertEqual((await self.runner.run(self.workflow([]))).status, WorkflowRunStatus.SUCCEEDED)

    async def test_event_callback_receives_ordered_state_changes(self):
        node = self.register("constant", self.constant)
        events = []
        run = await self.runner.run(self.workflow([node]), on_event=events.append)
        self.assertEqual(
            [(event.type, event.status.value) for event in events],
            [
                ("run_state_changed", "pending"),
                ("node_state_changed", "pending"),
                ("run_state_changed", "running"),
                ("node_state_changed", "running"),
                ("node_state_changed", "succeeded"),
                ("run_state_changed", "succeeded"),
            ],
        )
        self.assertTrue(all(event.run_id == run.id for event in events))
        self.assertTrue(all(event.node_id == node.id for event in events if event.type == "node_state_changed"))

    async def test_callback_exception_does_not_fail_workflow(self):
        def broken_callback(event):
            raise RuntimeError("observer failed")

        node = self.register("constant", self.constant)
        with self.assertLogs("mushin.models.run", level="ERROR") as logs:
            run = await self.runner.run(self.workflow([node]), on_event=broken_callback)
        self.assertEqual(run.status, WorkflowRunStatus.SUCCEEDED)
        self.assertEqual(len(logs.records), 6)

    async def test_callback_exception_preserves_failure_cleanup(self):
        events = []

        def broken_callback(event):
            events.append(event)
            raise RuntimeError("observer failed")

        async def fail(*args):
            raise ValueError("executor failed")

        root = self.register("fail", fail)
        child = self.register("child", self.constant, ("a",))
        with self.assertLogs("mushin.models.run", level="ERROR"):
            run = await self.runner.run(
                self.workflow([root, child], [self.edge(root, child)]), on_event=broken_callback,
            )
        self.assertEqual(run.status, WorkflowRunStatus.FAILED)
        self.assertIn("executor failed", run.error.message)
        self.assert_event_states(events, ["pending", "running", "failed"], {
            root.id: ["pending", "running", "failed"], child.id: ["pending", "blocked"],
        })

    async def test_concurrent_runs_keep_callbacks_separate(self):
        entered = asyncio.Event()
        release = asyncio.Event()

        async def wait(*args):
            entered.set()
            await release.wait()
            return {"out": 1}

        slow = self.register("slow", wait)
        fast = self.register("fast", self.constant)
        slow_events, fast_events = [], []
        task = asyncio.create_task(self.runner.run(self.workflow([slow]), on_event=slow_events.append))
        try:
            await asyncio.wait_for(entered.wait(), timeout=1)
            fast_run = await self.runner.run(self.workflow([fast]), on_event=fast_events.append)
        finally:
            release.set()
            slow_run = await task
        self.assertNotEqual(slow_run.id, fast_run.id)
        for run, events, node in ((slow_run, slow_events, slow), (fast_run, fast_events, fast)):
            self.assertTrue(all(event.run_id == run.id for event in events))
            self.assert_event_states(events, ["pending", "running", "succeeded"], {node.id: ["pending", "running", "succeeded"]})

    async def test_invalid_graph_never_executes(self):
        target = self.register("add", self.add, ("a", "b"))
        events = []
        run = await self.runner.run(self.workflow([target]), on_event=events.append)
        self.assertEqual(run.status, WorkflowRunStatus.FAILED)
        self.assertEqual(run.error.code, "invalid_workflow")
        self.assertEqual(len(run.validation_issues), 2)
        self.assertEqual(self.calls, [])
        self.assert_event_states(events, ["pending", "failed"], {target.id: ["pending", "cancelled"]})
        self.assertEqual(events[-1].error, run.error)

    async def test_failure_blocks_transitive_dependents(self):
        async def fail(*args):
            raise RuntimeError("broken")

        root = self.register("fail", fail)
        child = self.register("child", self.constant, ("a",))
        grandchild = self.register("grandchild", self.constant, ("a",))
        independent = self.register("independent", self.constant)
        events = []
        run = await self.runner.run(
            self.workflow([root, child, grandchild, independent], [self.edge(root, child), self.edge(child, grandchild)]),
            on_event=events.append,
        )
        self.assertEqual(run.status, WorkflowRunStatus.FAILED)
        self.assertEqual(run.nodes[root.id].status, NodeRunStatus.FAILED)
        self.assertEqual(run.nodes[child.id].status, NodeRunStatus.BLOCKED)
        self.assertEqual(run.nodes[grandchild.id].status, NodeRunStatus.BLOCKED)
        self.assertEqual(run.nodes[independent.id].status, NodeRunStatus.CANCELLED)
        self.assertIn("broken", run.error.message)
        self.assertEqual(self.calls, [])
        self.assert_event_states(events, ["pending", "running", "failed"], {
            root.id: ["pending", "running", "failed"],
            child.id: ["pending", "blocked"],
            grandchild.id: ["pending", "blocked"],
            independent.id: ["pending", "cancelled"],
        })
        failure = next(event for event in events if event.type == "node_state_changed" and event.status == NodeRunStatus.FAILED)
        self.assertEqual(failure.error, run.nodes[root.id].error)
        self.assertEqual(events[-1].error, run.error)
        # Observers may edit nested error objects without changing the run record.
        failure.error.message = "changed by observer"
        self.assertIn("broken", run.error.message)
        self.assertIn("broken", events[-1].error.message)

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
        events = []
        run = await self.runner.run(self.workflow([first]), cancellation=event, on_event=events.append)
        self.assertEqual(run.status, WorkflowRunStatus.CANCELLED)
        self.assertIsNone(run.nodes[first.id].started_at)
        self.assert_event_states(events, ["pending", "running", "cancelled"], {first.id: ["pending", "cancelled"]})

        async def stop(*args):
            event.set()
            return {"out": 1}

        event.clear()
        stopping = self.register("stop", stop)
        events.clear()
        run = await self.runner.run(self.workflow([stopping, first]), cancellation=event, on_event=events.append)
        self.assertEqual(run.nodes[stopping.id].status, NodeRunStatus.SUCCEEDED)
        self.assertEqual(run.nodes[first.id].status, NodeRunStatus.CANCELLED)
        self.assertEqual(self.calls, [])
        self.assert_event_states(events, ["pending", "running", "cancelled"], {
            stopping.id: ["pending", "running", "succeeded"], first.id: ["pending", "cancelled"],
        })

    async def test_cooperative_cancellation_inside_executor(self):
        async def stop(inputs, config, context):
            context.cancellation.set()
            context.raise_if_cancelled()

        node = self.register("stop", stop)
        events = []
        run = await self.runner.run(self.workflow([node]), on_event=events.append)
        self.assertEqual(run.status, WorkflowRunStatus.CANCELLED)
        self.assertEqual(run.nodes[node.id].status, NodeRunStatus.CANCELLED)
        self.assertIsNotNone(run.nodes[node.id].finished_at)
        self.assert_event_states(events, ["pending", "running", "cancelled"], {node.id: ["pending", "running", "cancelled"]})

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
        events = []
        task = asyncio.create_task(self.runner.run(self.workflow([node]), on_event=events.append))
        await asyncio.wait_for(started.wait(), timeout=1)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(cleaned_up.is_set())
        self.assert_event_states(events, ["pending", "running", "cancelled"], {node.id: ["pending", "running", "cancelled"]})

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
