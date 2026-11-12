"""Run with: python -m mushin.playground."""

from __future__ import annotations

import asyncio
import logging
from uuid import uuid4

from mushin.models.node import Edge, Node, Workflow
from mushin.models.registry import NodeRegistry
from mushin.models.run import WorkflowRunner, WorkflowRunStatus
from mushin.nodes import Add, Constant


async def main() -> None:
    registry = NodeRegistry()
    registry.register(Constant.spec, Constant())
    registry.register(Add.spec, Add())

    left = Node(id=uuid4(), type=Constant.spec.type, config={"value": 2})
    right = Node(id=uuid4(), type=Constant.spec.type, config={"value": 3})
    add = Node(id=uuid4(), type=Add.spec.type)
    workflow = Workflow(
        id=uuid4(),
        name="Add two constants",
        nodes=[left, right, add],
        edges=[
            Edge(source_node=left.id, source_port="value", target_node=add.id, target_port="a"),
            Edge(source_node=right.id, source_port="value", target_node=add.id, target_port="b"),
        ],
    )

    run = await WorkflowRunner(registry).run(workflow)
    if run.status != WorkflowRunStatus.SUCCEEDED:
        print(run.model_dump_json(indent=2))
        raise SystemExit(1)
    print(f"{left.config['value']} + {right.config['value']} = {run.nodes[add.id].outputs['sum']}")


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
