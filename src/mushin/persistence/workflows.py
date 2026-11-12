from __future__ import annotations

import asyncio
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from typing import Protocol
from uuid import UUID

import yaml
from pydantic import ValidationError

from mushin.models.node import Workflow
from mushin.models.workflow_document import WorkflowDocument


class WorkflowDocumentError(ValueError):
    """A stored workflow contains invalid YAML, model data, or an inconsistent ID."""


class WorkflowRepository(Protocol):
    """Persist drafts without requiring successful graph validation.

    save creates or replaces by ID (last completed writer wins). load and
    delete raise FileNotFoundError for missing workflows. list returns full
    documents in ID order and fails on an invalid stored document rather than
    silently hiding it. Filesystem errors propagate to the caller.
    """

    async def save(self, workflow: Workflow) -> None: ...

    async def load(self, workflow_id: UUID) -> Workflow: ...

    async def list(self) -> list[Workflow]: ...

    async def delete(self, workflow_id: UUID) -> None: ...


class _WorkflowLoader(yaml.SafeLoader):
    """Safe YAML loading with duplicate keys treated as document errors."""

    def construct_mapping(self, node, deep=False):
        self.flatten_mapping(node)
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                if key in mapping:
                    raise yaml.constructor.ConstructorError(
                        None,
                        None,
                        f"Duplicate YAML key: {key!r}",
                        key_node.start_mark,
                    )
                mapping[key] = self.construct_object(value_node, deep=deep)
            except TypeError as exc:
                raise yaml.constructor.ConstructorError(
                    None,
                    None,
                    "YAML mapping keys must be hashable",
                    key_node.start_mark,
                ) from exc
        return mapping


def parse_workflow_yaml(document: str) -> Workflow:
    """Parse named authoring YAML or an existing UUID-based workflow snapshot."""
    try:
        data = yaml.load(document, Loader=_WorkflowLoader)
        if isinstance(data, dict) and isinstance(data.get("nodes"), dict):
            return WorkflowDocument.model_validate(data).to_workflow()
        return Workflow.model_validate(data)
    except (yaml.YAMLError, ValidationError) as exc:
        raise WorkflowDocumentError(f"Invalid workflow document: {exc}") from exc


async def load_workflow_file(path: str | Path) -> Workflow:
    """Import an authored or UUID-based YAML file with any filename."""
    path = Path(path).absolute()

    def read() -> Workflow:
        try:
            return parse_workflow_yaml(path.read_text(encoding="utf-8"))
        except (WorkflowDocumentError, UnicodeError) as exc:
            raise WorkflowDocumentError(f"Invalid workflow document {path}: {exc}") from exc

    return await asyncio.to_thread(read)


class YamlWorkflowRepository:
    """Store UTF-8 YAML documents as <workflow UUID hex>.yaml in one directory.

    Serialization uses JSON-compatible model data so UUIDs remain plain strings
    and YAML contains no Python object tags. Saves atomically replace a complete
    document; comments and hand formatting are not preserved when resaving.

    File operations run off the event loop. Cancellation does not interrupt an
    active worker-thread write, which may still finish saving the document.
    """

    def __init__(self, root: str | Path) -> None:
        self._root = Path(root).absolute()

    def _path(self, workflow_id: UUID) -> Path:
        return self._root / f"{workflow_id.hex}.yaml"

    async def save(self, workflow: Workflow) -> None:
        # Snapshot before yielding so caller edits cannot affect the write.
        data = workflow.model_dump(mode="json")
        await asyncio.to_thread(self._save, self._path(workflow.id), data)

    def _save(self, path: Path, data: dict) -> None:
        document = yaml.safe_dump(data, sort_keys=False, allow_unicode=True)
        self._root.mkdir(parents=True, exist_ok=True)
        staging_path = None
        try:
            with NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=self._root,
                prefix=".pending-",
                delete=False,
            ) as staging:
                staging_path = Path(staging.name)
                staging.write(document)
                staging.flush()
                os.fsync(staging.fileno())
            os.replace(staging_path, path)
        finally:
            if staging_path is not None:
                staging_path.unlink(missing_ok=True)

    async def load(self, workflow_id: UUID) -> Workflow:
        return await asyncio.to_thread(self._load, workflow_id)

    def _load(self, workflow_id: UUID) -> Workflow:
        path = self._path(workflow_id)
        try:
            data = yaml.load(path.read_text(encoding="utf-8"), Loader=_WorkflowLoader)
            workflow = Workflow.model_validate(data)
            if workflow.id != workflow_id:
                raise WorkflowDocumentError(f"Workflow ID does not match filename: {path}")
            return workflow
        except (yaml.YAMLError, ValidationError, UnicodeError) as exc:
            raise WorkflowDocumentError(f"Invalid workflow document {path}: {exc}") from exc

    async def list(self) -> list[Workflow]:
        return await asyncio.to_thread(self._list)

    def _list(self) -> list[Workflow]:
        workflows = []
        for path in sorted(self._root.glob("*.yaml")):
            try:
                workflow_id = UUID(hex=path.stem)
            except ValueError:
                continue
            if path == self._path(workflow_id):
                workflows.append(self._load(workflow_id))
        return workflows

    async def delete(self, workflow_id: UUID) -> None:
        await asyncio.to_thread(self._path(workflow_id).unlink)
