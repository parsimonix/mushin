"""Run with: python -m mushin.playground."""

from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

from mushin.models.node import Node, PortDefault, Workflow, WorkflowInput, WorkflowOutput
from mushin.models.registry import NodeRegistry
from mushin.models.run import WorkflowRunner, WorkflowRunStatus
from mushin.nodes import Add


async def main() -> None:
    registry = NodeRegistry()
    # Customize this playground's registration without changing the built-in spec.
    add_spec = Add.spec.model_copy(deep=True)
    for port in add_spec.inputs:
        if port.name == "b":
            port.default = PortDefault(value=3)
    registry.register(add_spec, Add())

    add = Node(id=uuid4(), type=Add.spec.type)
    workflow = Workflow(
        id=uuid4(),
        name="Add two workflow inputs",
        nodes=[add],
        inputs=[
            WorkflowInput(name="left", target_node=add.id, target_port="a"),
            WorkflowInput(name="right", target_node=add.id, target_port="b"),
        ],
        outputs=[WorkflowOutput(name="sum", source_node=add.id, source_port="sum")],
    )

    # Bindings survive saving and loading the workflow document.
    workflow = Workflow.model_validate_json(workflow.model_dump_json())
    runner = WorkflowRunner(registry)
    for inputs in ({"left": 2}, {"left": 2, "right": 8}):
        run = await runner.run(workflow, inputs=inputs)
        if run.status != WorkflowRunStatus.SUCCEEDED:
            print(run.model_dump_json(indent=2))
            raise SystemExit(1)
        right = inputs.get("right", "default (3)")
        print(f"left={inputs['left']}, right={right} -> sum={run.outputs['sum']}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
