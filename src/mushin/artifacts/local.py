from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import AsyncGenerator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from pathlib import Path
from tempfile import NamedTemporaryFile, TemporaryDirectory
from typing import BinaryIO
from uuid import uuid4

from mushin.models.artifact import ArtifactRef


@dataclass
class ArtifactOutput:
    """Temporary output path and the reference assigned after successful exit."""

    path: Path
    ref: ArtifactRef | None = None


class LocalArtifactStore:
    """Store immutable content in a caller-owned workspace directory.

    Content paths depend only on opaque IDs, never logical filenames. Renamed
    references therefore resolve to the same bytes. This service owns files
    within its root; callers must not modify them directly. Workspace cleanup
    remains the caller's responsibility.

    File operations run in worker threads. Cancelling an await does not stop an
    active file operation, which may finish and leave unreferenced content.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).absolute()

    async def import_file(self, source: str | Path, *, filename: str | None = None) -> ArtifactRef:
        """Copy a source into managed storage without modifying the original."""
        source = Path(source).absolute()
        ref = ArtifactRef(content_id=uuid4(), filename=source.name if filename is None else filename)
        await asyncio.to_thread(self._import_file, source, ref)
        return ref

    async def create(self, content: bytes, *, filename: str) -> ArtifactRef:
        """Store newly produced bytes under a fresh content ID."""
        if not isinstance(content, bytes):
            raise TypeError("Artifact content must be bytes")
        ref = ArtifactRef(content_id=uuid4(), filename=filename)
        await asyncio.to_thread(self._publish, ref, lambda stream: stream.write(content))
        return ref

    async def read(self, ref: ArtifactRef) -> bytes:
        """Resolve a reference to bytes; raise FileNotFoundError if absent.

        Reads load the complete artifact into memory. Streaming access can be
        added when transformations need it.
        """
        return await asyncio.to_thread((self._root / ref.content_id.hex).read_bytes)

    async def size(self, ref: ArtifactRef) -> int:
        """Return stored content size without loading its bytes into memory."""
        stat = await asyncio.to_thread((self._root / ref.content_id.hex).stat)
        return stat.st_size

    @asynccontextmanager
    async def input_path(self, ref: ArtifactRef) -> AsyncGenerator[Path]:
        """Lease an isolated, extensionless input copy without reading into memory.

        The caller must stop and reap external processes before leaving the
        context. The workspace owner must keep the store root alive throughout
        all leases. Changes to this copy never affect stored content.
        """
        self._root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=self._root, prefix=".input-") as directory:
            path = Path(directory) / "content"
            copy = asyncio.create_task(asyncio.to_thread(
                shutil.copyfile, self._root / ref.content_id.hex, path
            ))
            try:
                await asyncio.shield(copy)
                yield path
            finally:
                # A worker copy must finish before its directory is removed,
                # including when the awaiting task is cancelled repeatedly.
                while not copy.done():
                    try:
                        await asyncio.shield(copy)
                    except asyncio.CancelledError:
                        continue
                if not copy.cancelled():
                    copy.exception()

    @asynccontextmanager
    async def output_file(self, *, filename: str) -> AsyncGenerator[ArtifactOutput]:
        """Publish output only on normal exit; exceptions discard partial files.

        The yielded path does not exist initially and has no extension: external
        tools must receive an explicit output format. Callers must finish and
        reap processes before exit and verify their output before returning.
        ``output.ref`` is assigned only after successful publication.
        """
        ref = ArtifactRef(content_id=uuid4(), filename=filename)
        self._root.mkdir(parents=True, exist_ok=True)
        with TemporaryDirectory(dir=self._root, prefix=".output-") as directory:
            output = ArtifactOutput(path=Path(directory) / "content")
            yield output
            # No await between publication and assigning the reference.
            if output.path.is_symlink() or not output.path.is_file():
                raise ValueError("Artifact output must be a regular file")
            os.link(output.path, self._root / ref.content_id.hex)
            output.ref = ref

    async def save(self, ref: ArtifactRef, destination: str | Path) -> Path:
        """Copy content into an existing directory, failing on any name collision.

        Publish only complete files. Existing files and symlinks are never
        replaced. Cancellation may finish after the file has been saved.
        """
        ref = ArtifactRef.model_validate(ref)
        return await asyncio.to_thread(self._save, ref, Path(destination).absolute())

    def _save(self, ref: ArtifactRef, destination: Path) -> Path:
        directory = destination.resolve(strict=True)
        target = directory / ref.filename
        with (self._root / ref.content_id.hex).open("rb") as incoming, NamedTemporaryFile(dir=directory, prefix=".pending-") as staging:
            shutil.copyfileobj(incoming, staging)
            staging.flush()
            os.link(staging.name, target)
        return target

    def _import_file(self, source: Path, ref: ArtifactRef) -> None:
        with source.open("rb") as incoming:
            self._publish(ref, lambda outgoing: shutil.copyfileobj(incoming, outgoing))

    def _publish(self, ref: ArtifactRef, write: Callable[[BinaryIO], object]) -> None:
        """Publish completed content atomically without replacing existing files."""
        self._root.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(dir=self._root, prefix=".pending-") as staging:
            write(staging)
            staging.flush()
            os.link(staging.name, self._root / ref.content_id.hex)
