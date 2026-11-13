import asyncio
import logging
import os
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from pydantic import ValidationError

from mushin.models.node import Node, Workflow, WorkflowInput, WorkflowOutput
from mushin.models.registry import NodeRegistry, create_default_type_registry
from mushin.models.run import NodeExecutionContext, WorkflowRunner, WorkflowRunStatus
from mushin.nodes import ScanFolder


class ScanFolderTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.workspace = TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.root = Path(self.workspace.name)
        self.context = NodeExecutionContext(run_id=uuid4(), node_id=uuid4(), logger=logging.getLogger('test'))

    async def scan(self, path=None):
        return await ScanFolder().execute({'path': str(self.root if path is None else path)}, {}, self.context)

    async def test_sorted_files_hidden_entries_and_source_preservation(self):
        for name in ('z.txt', 'a.txt', '.hidden'):
            (self.root / name).write_text(name)
        subfolder = self.root / 'nested'
        subfolder.mkdir()
        (subfolder / 'child.txt').write_text('child')
        (self.root / 'file-link').symlink_to(self.root / 'a.txt')
        (self.root / 'folder-link').symlink_to(subfolder, target_is_directory=True)
        (self.root / 'broken-link').symlink_to(self.root / 'missing')
        if hasattr(os, 'mkfifo'):
            os.mkfifo(self.root / 'pipe')
        (self.root / '.hidden-folder').mkdir()
        (self.root / '.hidden-link').symlink_to(subfolder, target_is_directory=True)
        result = await self.scan()
        self.assertEqual(result, {
            'files': [str(self.root / name) for name in ('.hidden', 'a.txt', 'z.txt')],
            'folders': [str(self.root / name) for name in ('.hidden-folder', 'nested')],
            'symlinks': [str(self.root / name) for name in ('.hidden-link', 'broken-link', 'file-link', 'folder-link')],
        })
        self.assertEqual((self.root / 'a.txt').read_text(), 'a.txt')
        self.assertEqual((subfolder / 'child.txt').read_text(), 'child')

    async def test_empty_folder_is_preserved(self):
        self.assertEqual(await self.scan(), {'files': [], 'folders': [], 'symlinks': []})
        self.assertTrue(self.root.is_dir())

    async def test_relative_input_returns_absolute_paths(self):
        (self.root / 'file').touch()
        self.assertEqual(await self.scan(os.path.relpath(self.root)), {'files': [str(self.root / 'file')], 'folders': [], 'symlinks': []})

    async def test_filesystem_errors_propagate(self):
        with self.assertRaises(FileNotFoundError):
            await self.scan(self.root / 'missing')
        (self.root / 'file').touch()
        with self.assertRaises(NotADirectoryError):
            await self.scan(self.root / 'file')
        with patch('mushin.nodes.folders.scan_folder.os.scandir', side_effect=PermissionError('denied')):
            with self.assertRaises(PermissionError):
                await self.scan()

    async def test_cancellation_before_and_during_scan(self):
        self.context.cancellation.set()
        with self.assertRaises(asyncio.CancelledError):
            await self.scan()
        self.context.cancellation.clear()
        original = os.scandir
        def cancel(path):
            self.context.cancellation.set()
            return original(path)
        (self.root / 'file').touch()
        with patch('mushin.nodes.folders.scan_folder.os.scandir', side_effect=cancel):
            with self.assertRaises(asyncio.CancelledError):
                await self.scan()

    async def test_workflow_validates_collection_output_and_records_failure(self):
        registry = NodeRegistry()
        registry.register(ScanFolder.spec, ScanFolder())
        node = Node(type=ScanFolder.spec.type)
        workflow = Workflow(name='Scan', nodes=[node],
            inputs=[WorkflowInput(name='path', target_node=node.id, target_port='path')],
            outputs=[WorkflowOutput(name=name, source_node=node.id, source_port=name)
                     for name in ('files', 'folders', 'symlinks')])
        runner = WorkflowRunner(registry)
        (self.root / 'file').touch()
        run = await runner.run(workflow, inputs={'path': str(self.root)})
        self.assertEqual(run.status, WorkflowRunStatus.SUCCEEDED)
        self.assertEqual(run.outputs, {'files': [str(self.root / 'file')], 'folders': [], 'symlinks': []})
        run = await runner.run(workflow, inputs={'path': str(self.root / 'missing')})
        self.assertEqual(run.status, WorkflowRunStatus.FAILED)
        self.assertEqual(run.error.code, 'node_execution_failed')
        self.assertEqual(run.outputs, {})
        types = create_default_type_registry()
        for invalid in (True, 'file', [1]):
            with self.assertRaises(ValidationError):
                types.validate('string_list', invalid)
