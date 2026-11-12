from __future__ import annotations

import asyncio
import os
import shutil
from collections.abc import Callable
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import BinaryIO
from uuid import uuid4

from mushin.models.artifact import ArtifactRef


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
        with (self._root / ref.content_id.hex).open("rb") as incoming:
            with NamedTemporaryFile(dir=directory, prefix=".pending-") as staging:
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
