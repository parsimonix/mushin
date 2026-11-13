"""Run with: python -m mushin.playground."""

from __future__ import annotations

import asyncio
import logging
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory

from mushin.artifacts import LocalArtifactStore
from mushin.enums import NodeRunStatus
from mushin.models.events import ExecutionEvent, NodeProgressEvent, NodeStateEvent
from mushin.models.node import Edge, Node, Workflow, WorkflowInput, WorkflowOutput
from mushin.models.registry import NodeRegistry
from mushin.models.run import WorkflowRunner, WorkflowRunStatus
from mushin.nodes import GuessVideo, ImportFile, RenameFile, SaveFile, TemplatedString, to_variables_registration
from mushin.types.video_metadata import VideoMetadata


def print_event(event: ExecutionEvent, *, workflow: Workflow) -> None:
    """Show node states and incoming graph routes when a node starts."""
    nodes = {node.id: node for node in workflow.nodes}

    def label(node: Node) -> str:
        return node.name or node.type

    subject = f"run={event.run_id.hex[:8]}"
    if isinstance(event, NodeProgressEvent):
        percentage = event.percentage
        value = f"{percentage:.1f}%" if percentage is not None else f"{event.completed:.3f} {event.unit} (total unknown)"
        print(f"[{event.timestamp.isoformat()}] {subject} node={label(nodes[event.node_id])} progress={value}", flush=True)
        return
    if isinstance(event, NodeStateEvent):
        node = nodes[event.node_id]
        subject += f" node={label(node)}[{event.node_id.hex[:8]}]"
    print(f"[{event.timestamp.isoformat()}] {subject} status={event.status}", flush=True)
    if isinstance(event, NodeStateEvent) and event.status == NodeRunStatus.RUNNING:
        for binding in workflow.inputs:
            if binding.target_node == event.node_id:
                print(f"  input route: workflow.{binding.name} -> {label(node)}.{binding.target_port}", flush=True)
        for edge in workflow.edges:
            if edge.target_node == event.node_id:
                print(
                    f"  input route: {label(nodes[edge.source_node])}.{edge.source_port} -> {label(node)}.{edge.target_port}",
                    flush=True,
                )
    if event.error is not None:
        print(f"  {event.error.code}: {event.error.message}", flush=True)


def build_workflow(variables_type: str) -> Workflow:
    """Compose the graph separately from workspace setup and execution."""
    source = Node(name="ImportFile", type=ImportFile.spec.type)
    guess = Node(name="GuessVideo", type=GuessVideo.spec.type, config={"type": "movie"})
    variables = Node(
        name="ToVariables",
        type=variables_type,
        config={"fields": ["title", "year", "container"], "omit_none": True},
    )
    template = Node(
        name="TemplatedString",
        type=TemplatedString.spec.type,
        config={"template": "{title} ({year}).{container}"},
    )
    rename = Node(name="RenameFile", type=RenameFile.spec.type)
    save = Node(name="SaveFile", type=SaveFile.spec.type)
    return Workflow(
        name="Generate a Plex movie filename",
        nodes=[source, guess, variables, template, rename, save],
        edges=[
            Edge(source_node=upstream.id, source_port=output, target_node=downstream.id, target_port=input_port)
            for upstream, output, downstream, input_port in (
                (source, "file", guess, "file"),
                (guess, "metadata", variables, "value"),
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
        source = root / "The.Matrix.1999.1080p.BluRay.x264.mkv"
        # GuessIt parses the name only; these are placeholder bytes, not video.
        source.write_bytes(b"Dummy video content for the Mushin playground.\n")
        destination = root / "output"
        destination.mkdir()
        store = LocalArtifactStore(root / "artifacts")
        registry = NodeRegistry()
        for node_class in (ImportFile, SaveFile):
            registry.register(node_class.spec, node_class(store))
        for node_class in (GuessVideo, TemplatedString, RenameFile):
            registry.register(node_class.spec, node_class())
        adapter = to_variables_registration(VideoMetadata, input_type="video_metadata")
        registry.register(adapter.spec, adapter.executor)

        workflow = build_workflow(adapter.spec.type)
        # Bindings and the configured template survive saving and loading.
        workflow = Workflow.model_validate_json(workflow.model_dump_json())
        print("Import -> GuessVideo -> variables -> template -> rename -> save", flush=True)
        run = await WorkflowRunner(registry).run(
            workflow,
            inputs={"source": str(source), "destination": str(destination)},
            on_event=partial(print_event, workflow=workflow),
        )
        if run.status != WorkflowRunStatus.SUCCEEDED:
            print(run.model_dump_json(indent=2))
            raise SystemExit(1)

        saved = Path(str(run.outputs["saved_path"]))
        if saved.name != "The Matrix (1999).mkv":
            raise RuntimeError(f"Unexpected Plex filename: {saved.name}")
        if saved.read_bytes() != source.read_bytes():
            raise RuntimeError("Saved content differs from the source")
        print(f"Plex movie filename: {run.outputs['filename']}")
        print(f"Saved to: {saved}")
        print("Verified saved content matches the source.")
        print("The temporary sample workspace is removed when the playground exits.")


if __name__ == "__main__":
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    asyncio.run(main())
