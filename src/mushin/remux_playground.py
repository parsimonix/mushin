"""Run with: python -m mushin.remux_playground [--output ./remux-output]."""

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
from mushin.media import MediaProbe, MediaTools, ProcessRunner
from mushin.media.container import matches_container
from mushin.models.artifact import ArtifactRef
from mushin.models.node import Edge, Node, Workflow, WorkflowInput, WorkflowOutput
from mushin.models.registry import NodeRegistry
from mushin.models.run import WorkflowRunner, WorkflowRunStatus
from mushin.nodes import ImportFile, RemuxVideo, SaveFile
from mushin.playground import print_event


def build_workflow() -> Workflow:
    """Include a second remux to demonstrate idempotency inside the graph."""
    source = Node(name="Import MP4", type=ImportFile.spec.type)
    remux = Node(name="Remux to MKV", type=RemuxVideo.spec.type, config={"container": "mkv"})
    repeat = Node(name="Remux MKV again", type=RemuxVideo.spec.type, config={"container": "mkv"})
    save = Node(name="Save MKV", type=SaveFile.spec.type)
    return Workflow(
        name="Import, remux, and save a valid video",
        nodes=[source, remux, repeat, save],
        edges=[
            Edge(source_node=upstream.id, source_port="file", target_node=downstream.id, target_port="file")
            for upstream, downstream in ((source, remux), (remux, repeat), (repeat, save))
        ],
        inputs=[
            WorkflowInput(name="source", target_node=source.id, target_port="source"),
            WorkflowInput(name="destination", target_node=save.id, target_port="directory"),
        ],
        outputs=[
            WorkflowOutput(name="imported", source_node=source.id, source_port="file"),
            WorkflowOutput(name="remuxed", source_node=remux.id, source_port="file"),
            WorkflowOutput(name="repeated", source_node=repeat.id, source_port="file"),
            WorkflowOutput(name="saved_path", source_node=save.id, source_port="path"),
        ],
    )


async def generate_sample(tools: MediaTools, path: Path) -> None:
    """Generate a short moving test pattern with a tone; download nothing."""
    tools.require(muxers=["mp4", "matroska"], demuxers=["lavfi"], encoders=["mpeg4", "aac"])
    await ProcessRunner().ffmpeg(
        tools,
        ["-v", "error", "-n", "-f", "lavfi", "-i", "testsrc2=size=160x90:rate=10:duration=2",
         "-f", "lavfi", "-i", "sine=frequency=440:sample_rate=48000:duration=2",
         "-map", "0:v:0", "-map", "1:a:0", "-c:v", "mpeg4", "-q:v", "5", "-threads:v", "1",
         "-c:a", "aac", "-f", "mp4", str(path)],
        timeout=30,
    )


def _digest(path: Path) -> bytes:
    # Hash in chunks so verification does not require loading video into memory.
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").digest()


async def run_sample(tools: MediaTools, workspace: Path, destination: Path) -> Path:
    """Generate and verify the complete sample in a caller-owned workspace."""
    destination.mkdir(parents=True, exist_ok=True)
    source = workspace / "sample.mp4"
    print("Generating a two-second MP4 with video and audio...", flush=True)
    await generate_sample(tools, source)
    original_digest = _digest(source)
    store_root = workspace / "artifacts"
    store = LocalArtifactStore(store_root)
    registry = NodeRegistry()
    registry.register(ImportFile.spec, ImportFile(store))
    registry.register(RemuxVideo.spec, RemuxVideo(store, tools))
    registry.register(SaveFile.spec, SaveFile(store))
    workflow = build_workflow()
    print("Import MP4 -> remux to MKV -> remux MKV again -> save", flush=True)
    run = await WorkflowRunner(registry).run(
        workflow,
        inputs={"source": str(source), "destination": str(destination)},
        on_event=partial(print_event, workflow=workflow),
    )
    if run.status != WorkflowRunStatus.SUCCEEDED:
        message = run.error.message if run.error is not None else str(run.status)
        raise RuntimeError(f"Sample workflow failed: {message}")
    imported = cast(ArtifactRef, run.outputs["imported"])
    remuxed = cast(ArtifactRef, run.outputs["remuxed"])
    repeated = cast(ArtifactRef, run.outputs["repeated"])
    saved = Path(cast(str, run.outputs["saved_path"]))
    if imported.content_id == remuxed.content_id or repeated != remuxed:
        raise RuntimeError("Expected one new artifact, followed by an unchanged reference")
    if len(list(store_root.iterdir())) != 2:
        raise RuntimeError("Expected exactly two artifacts and no remaining staging files")
    async with store.input_path(imported) as path:
        if _digest(path) != original_digest or _digest(source) != original_digest:
            raise RuntimeError("Source or imported content changed")
    # Remuxing changes container bytes; the saved copy must match the remuxed
    # artifact, while source preservation is checked separately above.
    async with store.input_path(remuxed) as path:
        if _digest(path) != _digest(saved):
            raise RuntimeError("Saved content differs from the remuxed artifact")
    probe = MediaProbe(tools)
    before = await probe.probe_path(source)
    after = await probe.probe_path(saved)
    if not matches_container(saved, after, "matroska"):
        raise RuntimeError("Saved file is not Matroska")
    expected = [(stream.codec_type, stream.codec_name) for stream in before.streams]
    if [(stream.codec_type, stream.codec_name) for stream in after.streams] != expected:
        raise RuntimeError("Saved streams or codecs differ from the source")
    # AAC encoder delay affects container duration; compare against the probed
    # source rather than assuming the generated file is exactly two seconds.
    if before.duration is None or after.duration is None or abs(before.duration - after.duration) > 0.1:
        raise RuntimeError("Saved duration differs from the source by more than 100 ms")
    print(f"Saved to: {saved}")
    print(f"Verified Matroska: {after.duration:.3f}s; " + ", ".join(f"{kind}={codec}" for kind, codec in expected))
    print("Verified source unchanged and saved bytes match the remuxed artifact.")
    print("Verified second MKV remux returned the same artifact; only two artifacts were stored.")
    return saved


async def main(tools: MediaTools, *, output: Path | None = None) -> None:
    print(tools.ffmpeg.version)
    print(tools.ffprobe.version)
    with TemporaryDirectory(prefix="mushin-remux-playground-") as directory:
        workspace = Path(directory)
        destination = workspace / "output" if output is None else output.absolute()
        await run_sample(tools, workspace, destination)
        if output is None:
            print("The temporary sample and output are removed on exit. Use --output to keep the MKV.")
        else:
            print("The saved MKV is retained; temporary source and artifacts are removed on exit.")


def cli() -> None:
    parser = argparse.ArgumentParser(description="Generate an MP4, remux it to MKV, and verify the saved output.")
    parser.add_argument("--output", type=Path, metavar="DIRECTORY", help="Keep sample.mkv in this directory; existing files are never replaced.")
    arguments = parser.parse_args()
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")
    try:
        # Discovery is synchronous startup work, before running the async graph.
        tools = MediaTools.discover()
        asyncio.run(main(tools, output=arguments.output))
    except (OSError, RuntimeError) as exc:
        parser.exit(1, f"Remux sample failed: {exc}\n")


if __name__ == "__main__":
    cli()
