"""Run with: python -m mushin.playground."""

from __future__ import annotations

import asyncio
import logging
from pathlib import Path
from tempfile import TemporaryDirectory

from mushin.artifacts import LocalArtifactStore
from mushin.models.file_metadata import FileMetadata
from mushin.models.events import ExecutionEvent, NodeStateEvent
from mushin.models.node import Edge, Node, Workflow, WorkflowInput, WorkflowOutput
from mushin.models.registry import NodeRegistry
from mushin.models.run import WorkflowRunner, WorkflowRunStatus
from mushin.nodes import ImportFile, InspectFile, RenameFile, SaveFile, TemplatedString, to_variables_registration


def print_event(event: ExecutionEvent) -> None:
    """Display progress as the runner delivers each notification."""
    subject = f"run={event.run_id}"
    if isinstance(event, NodeStateEvent):
        subject += f" node={event.node_id}"
    print(f"[{event.timestamp.isoformat()}] {subject} status={event.status}", flush=True)
    if event.error is not None:
        print(f"  {event.error.code}: {event.error.message}", flush=True)


def build_workflow(variables_type: str) -> Workflow:
    """Compose the graph separately from workspace setup and execution."""
    source = Node(type=ImportFile.spec.type)
    inspect = Node(type=InspectFile.spec.type)
    variables = Node(type=variables_type)
    template = Node(
        type=TemplatedString.spec.type,
        config={"template": "{stem}_{size_bytes}{extension}"},
    )
    rename = Node(type=RenameFile.spec.type)
    save = Node(type=SaveFile.spec.type)
    return Workflow(
        name="Name and save an artifact",
        nodes=[source, inspect, variables, template, rename, save],
        edges=[
            Edge(source_node=upstream.id, source_port=output, target_node=downstream.id, target_port=input_port)
            for upstream, output, downstream, input_port in (
                (source, "file", inspect, "file"),
                (inspect, "metadata", variables, "value"),
                (variables, "variables", template, "variables"),
                (template, "text", rename, "filename"),
                (source, "file", rename, "file"),
                (rename, "file", save, "file"),
            )
        ],
        inputs=[
            WorkflowInput(name="source", target_node=source.id, target_port="source"),
            WorkflowInput(name="destination", target_node=save.id, target_port="directory"),
        ],
        outputs=[
            WorkflowOutput(name="filename", source_node=template.id, source_port="text"),
            WorkflowOutput(name="saved_path", source_node=save.id, source_port="path"),
        ],
    )


async def main() -> None:
    # The sample, managed artifacts, and saved output share a disposable workspace.
    with TemporaryDirectory(prefix="mushin-playground-") as workspace:
        root = Path(workspace)
        source = root / "example.txt"
        source.write_bytes(b"Hello from Mushin!\n")
        destination = root / "output"
        destination.mkdir()
        store = LocalArtifactStore(root / "artifacts")
        registry = NodeRegistry()
        for node_class in (ImportFile, InspectFile, SaveFile):
            registry.register(node_class.spec, node_class(store))
        for node_class in (TemplatedString, RenameFile):
            registry.register(node_class.spec, node_class())
        adapter = to_variables_registration(FileMetadata, input_type="file_metadata")
        registry.register(adapter.spec, adapter.executor)

        workflow = build_workflow(adapter.spec.type)
        # Bindings and the configured template survive saving and loading.
        workflow = Workflow.model_validate_json(workflow.model_dump_json())
        print("Import -> inspect -> variables -> template -> rename -> save", flush=True)
        run = await WorkflowRunner(registry).run(
            workflow, inputs={"source": str(source), "destination": str(destination)}, on_event=print_event,
        )
        if run.status != WorkflowRunStatus.SUCCEEDED:
            print(run.model_dump_json(indent=2))
            raise SystemExit(1)

        saved = Path(str(run.outputs["saved_path"]))
        if saved.read_bytes() != source.read_bytes():
            raise RuntimeError("Saved content differs from the source")
        print(f"Generated filename: {run.outputs['filename']}")
        print(f"Saved to: {saved}")
        print("Verified saved content matches the source.")
        print("The temporary sample workspace is removed when the playground exits.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
