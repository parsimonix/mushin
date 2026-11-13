"""Run with: python -m mushin.plex_playground --input EPISODE [--output DIRECTORY]."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import logging
from functools import partial
from pathlib import Path
from tempfile import TemporaryDirectory
from typing import cast

from mushin.artifacts import LocalArtifactStore
from mushin.media import MediaProbe, MediaTools
from mushin.media.container import matches_container
from mushin.models.artifact import ArtifactRef
from mushin.models.node import Edge, Node, Workflow, WorkflowInput, WorkflowOutput
from mushin.models.registry import NodeRegistry
from mushin.models.run import WorkflowRunner, WorkflowRunStatus
from mushin.nodes import GuessVideo, ImportFile, RemuxVideo, RenameFile, SaveFile, TemplatedString, to_variables_registration
from mushin.playground import print_event
from mushin.types.video_metadata import VideoMetadata


def build_workflow(variables_type: str) -> Workflow:
    """Remux first so filename inference sees the destination extension."""
    source = Node(name="Import Video", type=ImportFile.spec.type)
    remux_to_mkv = Node(name="Remux to MKV", type=RemuxVideo.spec.type, config={"container": "mkv"})
    guess_metadata = Node(
        name="Guess Metadata",
        type=GuessVideo.spec.type,
        config={"type": "episode", "series_title": "The Apothecary Diaries"},
    )
    expose_metadata = Node(
        name="ToVariables",
        type=variables_type,
        config={"fields": ["title", "year", "season", "episode", "episode_title", "container"], "omit_none": True},
    )
    gen_plex_filename = Node(
        name="Generate Plex Filename",
        type=TemplatedString.spec.type,
        config={
            "template": "{title}{year_suffix} - s{season:02d}e{episode:02d}{episode_suffix}.{container}",
            "optional_sections": {
                "year_suffix": " ({year})",
                "episode_suffix": " - {episode_title}",
            },
        },
    )
    rename_video_file = Node(name="Rename Video File", type=RenameFile.spec.type)
    save_file = Node(name="Save Video File", type=SaveFile.spec.type, config={"overwrite": True})
    return Workflow(
        name="Process TV Episode for Plex",
        nodes=[source, remux_to_mkv, guess_metadata, expose_metadata, gen_plex_filename, rename_video_file, save_file],
        edges=[
            Edge(source_node=upstream.id, source_port=output_port, target_node=downstream.id, target_port=input_port)
            for upstream, output_port, downstream, input_port in (
                (source, "file", remux_to_mkv, "file"),
                (remux_to_mkv, "file", guess_metadata, "file"),
                (guess_metadata, "metadata", expose_metadata, "value"),
                (expose_metadata, "variables", gen_plex_filename, "variables"),
                (gen_plex_filename, "text", rename_video_file, "filename"),
                # Naming produces text; the artifact itself takes a separate edge.
                (remux_to_mkv, "file", rename_video_file, "file"),
                (rename_video_file, "file", save_file, "file"),
            )
        ],
        inputs=[
            WorkflowInput(name="source", target_node=source.id, target_port="source"),
            WorkflowInput(name="destination", target_node=save_file.id, target_port="directory"),
        ],
        outputs=[
            WorkflowOutput(name="imported", source_node=source.id, source_port="file"),
            WorkflowOutput(name="remuxed", source_node=remux_to_mkv.id, source_port="file"),
            WorkflowOutput(name="guessed", source_node=guess_metadata.id, source_port="metadata"),
            WorkflowOutput(name="variables", source_node=expose_metadata.id, source_port="variables"),
            WorkflowOutput(name="filename", source_node=gen_plex_filename.id, source_port="text"),
            WorkflowOutput(name="renamed", source_node=rename_video_file.id, source_port="file"),
            WorkflowOutput(name="saved_path", source_node=save_file.id, source_port="path"),
        ],
    )


def _digest(path: Path) -> bytes:
    # Hash in chunks rather than loading an entire episode into memory.
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").digest()


async def run_sample(tools: MediaTools, workspace: Path, destination: Path, *, source_file: Path) -> Path:
    """Import a supplied episode, run the graph, and verify the saved media."""
    source = source_file.absolute()
    if not source.is_file():
        raise FileNotFoundError(f"Source file does not exist or is not a regular file: {source}")
    print(f"Importing: {source}", flush=True)
    destination.mkdir(parents=True, exist_ok=True)
    original_digest = _digest(source)
    store = LocalArtifactStore(workspace / "artifacts")
    registry = NodeRegistry()
    for node_class in (ImportFile, SaveFile):
        registry.register(node_class.spec, node_class(store))
    registry.register(RemuxVideo.spec, RemuxVideo(store, tools))
    for node_class in (GuessVideo, TemplatedString, RenameFile):
        registry.register(node_class.spec, node_class())
    adapter = to_variables_registration(VideoMetadata, input_type="video_metadata")
    registry.register(adapter.spec, adapter.executor)
    workflow = build_workflow(adapter.spec.type)
    print("Import -> remux to MKV -> guess episode metadata -> variables -> filename -> rename -> save", flush=True)
    run = await WorkflowRunner(registry).run(
        workflow,
        inputs={"source": str(source), "destination": str(destination)},
        on_event=partial(print_event, workflow=workflow),
    )
    if run.status != WorkflowRunStatus.SUCCEEDED:
        message = run.error.message if run.error is not None else str(run.status)
        raise RuntimeError(f"Plex workflow failed: {message}")
    imported = cast(ArtifactRef, run.outputs["imported"])
    remuxed = cast(ArtifactRef, run.outputs["remuxed"])
    renamed = cast(ArtifactRef, run.outputs["renamed"])
    metadata = cast(VideoMetadata, run.outputs["guessed"])
    saved = Path(cast(str, run.outputs["saved_path"]))
    if run.outputs["filename"] != saved.name:
        raise RuntimeError(f"Unexpected Plex filename: {saved.name}")
    # Existing MKV inputs may pass through remuxing without creating new content.
    if remuxed.content_id != renamed.content_id:
        raise RuntimeError("Unexpected remux or rename artifact identity")
    async with store.input_path(imported) as path:
        if _digest(path) != original_digest or _digest(source) != original_digest:
            raise RuntimeError("Source or imported content changed")
    # Compare saved bytes with the renamed artifact, not the source video:
    # remuxing changes container bytes, while renaming changes only the reference.
    async with store.input_path(renamed) as path:
        if _digest(path) != _digest(saved):
            raise RuntimeError("Saved content differs from the renamed artifact")
    probe = MediaProbe(tools)
    before = await probe.probe_path(source)
    after = await probe.probe_path(saved)
    if not matches_container(saved, after, "matroska"):
        raise RuntimeError("Saved file is not Matroska")
    if [(s.codec_type, s.codec_name) for s in before.streams] != [(s.codec_type, s.codec_name) for s in after.streams]:
        raise RuntimeError("Saved streams or codecs differ from the source")
    # AAC delay is already present in the encoded source; use its probed duration.
    if before.duration is not None and after.duration is not None and abs(before.duration - after.duration) > 0.1:
        raise RuntimeError("Saved duration differs from the source by more than 100 ms")
    print(f"Guessed episode metadata: {metadata.model_dump_json(exclude_none=True)}")
    print(f"Plex episode filename: {saved.name}")
    print(f"Saved to: {saved}")
    duration = "unknown duration" if after.duration is None else f"{after.duration:.3f}s"
    print(f"Verified Matroska: {duration}; source and codecs unchanged; saved bytes match the artifact.")
    return saved


async def main(tools: MediaTools, *, input: Path, output: Path | None = None) -> None:
    print(tools.ffmpeg.version)
    print(tools.ffprobe.version)
    with TemporaryDirectory(prefix="mushin-plex-playground-") as directory:
        workspace = Path(directory)
        destination = workspace / "output" if output is None else output.absolute()
        await run_sample(tools, workspace, destination, source_file=input)
        if output is None:
            print("The temporary workspace and saved episode are removed on exit. Use --output to keep the MKV.")
        else:
            print("The named MKV is retained; the temporary workspace is removed on exit. Supplied source files remain unchanged.")


def cli() -> None:
    parser = argparse.ArgumentParser(description="Import a video, remux to MKV, and name it using episode metadata.")
    parser.add_argument("--input", "--source", type=Path, required=True, metavar="FILE", help="Import this episode file. The original is unchanged.")
    parser.add_argument("--output", type=Path, metavar="DIRECTORY", help="Keep the named MKV here; existing files are never replaced.")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(message)s")
    try:
        tools = MediaTools.discover()
        asyncio.run(main(tools, input=arguments.input, output=arguments.output))
    except (OSError, RuntimeError) as exc:
        parser.exit(1, f"Plex sample failed: {exc}\n")


def cli_multifile() -> None:
    parser = argparse.ArgumentParser(description="Import multiple videos, remux to MKV, and name them using episode metadata.")
    parser.add_argument("--input", metavar="DIRECTORY", required=True, help="Import all video files in this directory. Originals are unchanged.")
    parser.add_argument("--output", metavar="DIRECTORY", help="Keep the named MKVs here; existing files are never replaced.")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.DEBUG, format="%(levelname)s %(name)s: %(levelname)s %(name)s: %(message)s")
    try:
        tools = MediaTools.discover()
        input_dir = Path(arguments.input)
        if not input_dir.is_dir():
            parser.exit(1, f"Input is not a directory: {input_dir}")
        output_dir = Path(arguments.output) if arguments.output is not None else None
        for source in sorted(input_dir.iterdir()):
            if not source.is_file():
                continue
            print(f"\nProcessing {source} ...", flush=True)
            asyncio.run(main(tools, input=source, output=output_dir))
    except (OSError, RuntimeError) as exc:
        parser.exit(1, f"Plex sample failed: {exc}\n")


if __name__ == "__main__":
    # cli()
    cli_multifile()
