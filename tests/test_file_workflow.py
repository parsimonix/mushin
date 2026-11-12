import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from mushin.artifacts import LocalArtifactStore
from mushin.models.artifact import ArtifactRef
from mushin.models.node import Edge, Node, Workflow, WorkflowInput, WorkflowOutput
from mushin.models.registry import NodeRegistry
from mushin.models.run import NodeRunStatus, WorkflowRunner, WorkflowRunStatus
from mushin.nodes import ImportFile, InspectFile, RenameFile, SaveFile, TemplatedString, to_variables_registration
from mushin.types.file_metadata import FileMetadata


class FileWorkflowTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.base = Path(workspace.name)
        self.source = self.base / "source.bin"
        self.source.write_bytes(b"\x00\xffexample")
        self.destination = self.base / "destination"
        self.destination.mkdir()
        self.store = LocalArtifactStore(self.base / "artifacts")
        registry = NodeRegistry()
        registry.register(ImportFile.spec, ImportFile(self.store))
        registry.register(SaveFile.spec, SaveFile(self.store))
        registry.register(TemplatedString.spec, TemplatedString())
        registry.register(RenameFile.spec, RenameFile())
        registry.register(InspectFile.spec, InspectFile(self.store))
        self.variables_registration = to_variables_registration(FileMetadata, input_type="file_metadata")
        registry.register(self.variables_registration.spec, self.variables_registration.executor)
        self.runner = WorkflowRunner(registry)
        self.import_node = Node(id=uuid4(), type=ImportFile.spec.type)
        self.save_node = Node(id=uuid4(), type=SaveFile.spec.type)
        self.workflow = Workflow(
            id=uuid4(),
            name="Copy a file",
            nodes=[self.save_node, self.import_node],
            edges=[Edge(source_node=self.import_node.id, source_port="file", target_node=self.save_node.id, target_port="file")],
            inputs=[
                WorkflowInput(name="source", target_node=self.import_node.id, target_port="source"),
                WorkflowInput(name="destination", target_node=self.save_node.id, target_port="directory"),
            ],
            outputs=[WorkflowOutput(name="saved_path", source_node=self.save_node.id, source_port="path")],
        )

    async def run_workflow(self):
        return await self.runner.run(self.workflow, inputs={"source": str(self.source), "destination": str(self.destination)})

    async def test_import_and_save_workflow(self):
        run = await self.run_workflow()
        self.assertEqual(run.status, WorkflowRunStatus.SUCCEEDED)
        target = self.destination / self.source.name
        self.assertEqual(run.outputs, {"saved_path": str(target)})
        self.assertEqual(target.read_bytes(), self.source.read_bytes())
        ref = run.nodes[self.import_node.id].outputs["file"]
        target.write_bytes(b"changed")
        self.assertEqual(await self.store.read(ref), b"\x00\xffexample")
        self.assertEqual(self.source.read_bytes(), b"\x00\xffexample")

    async def test_metadata_drives_filename_through_complete_workflow(self):
        inspect = Node(id=uuid4(), type=InspectFile.spec.type)
        variables = Node(id=uuid4(), type=self.variables_registration.spec.type)
        naming = Node(id=uuid4(), type=TemplatedString.spec.type, config={"template": "{stem}_{size_bytes}{extension}"})
        rename = Node(id=uuid4(), type=RenameFile.spec.type)
        self.workflow.nodes.extend([rename, naming, variables, inspect])
        self.workflow.edges = [
            Edge(source_node=source.id, source_port=source_port, target_node=target.id, target_port=target_port)
            for source, source_port, target, target_port in (
                (self.import_node, "file", inspect, "file"),
                (inspect, "metadata", variables, "value"),
                (variables, "variables", naming, "variables"),
                (naming, "text", rename, "filename"),
                (self.import_node, "file", rename, "file"),
                (rename, "file", self.save_node, "file"),
            )
        ]
        # Exercise serialized graph bindings as well as runtime structured values.
        self.workflow = Workflow.model_validate_json(self.workflow.model_dump_json())
        run = await self.run_workflow()
        self.assertEqual(run.status, WorkflowRunStatus.SUCCEEDED)
        expected = self.destination / "source_9.bin"
        self.assertEqual(run.outputs, {"saved_path": str(expected)})
        self.assertEqual(expected.read_bytes(), self.source.read_bytes())
        self.assertEqual(
            run.nodes[variables.id].outputs["variables"],
            {
                "filename": "source.bin",
                "stem": "source",
                "extension": ".bin",
                "size_bytes": 9,
            },
        )
        self.assertEqual(
            run.nodes[rename.id].outputs["file"].content_id,
            run.nodes[self.import_node.id].outputs["file"].content_id,
        )
        self.assertEqual(len(list((self.base / "artifacts").iterdir())), 1)

    async def test_collision_preserves_existing_file(self):
        target = self.destination / self.source.name
        target.write_bytes(b"existing")
        run = await self.run_workflow()
        self.assertEqual(run.status, WorkflowRunStatus.FAILED)
        self.assertEqual(run.error.node_id, self.save_node.id)
        self.assertIn("FileExistsError", run.error.message)
        self.assertEqual(target.read_bytes(), b"existing")
        self.assertEqual(list(self.destination.iterdir()), [target])

    async def test_generated_name_rename_and_save(self):
        naming = Node(id=uuid4(), type=TemplatedString.spec.type, config={"template": "{title}_{year}.bin"})
        rename = Node(id=uuid4(), type=RenameFile.spec.type)
        self.workflow.nodes.extend([rename, naming])
        self.workflow.edges = [
            Edge(source_node=self.import_node.id, source_port="file", target_node=rename.id, target_port="file"),
            Edge(source_node=naming.id, source_port="text", target_node=rename.id, target_port="filename"),
            Edge(source_node=rename.id, source_port="file", target_node=self.save_node.id, target_port="file"),
        ]
        self.workflow.inputs.append(WorkflowInput(name="variables", target_node=naming.id, target_port="variables"))
        run = await self.runner.run(
            self.workflow,
            inputs={
                "source": str(self.source),
                "destination": str(self.destination),
                "variables": {"title": "report", "year": 2026},
            },
        )
        self.assertEqual(run.status, WorkflowRunStatus.SUCCEEDED)
        original = run.nodes[self.import_node.id].outputs["file"]
        renamed = run.nodes[rename.id].outputs["file"]
        self.assertEqual(original.filename, "source.bin")
        self.assertEqual(renamed.filename, "report_2026.bin")
        self.assertEqual(original.content_id, renamed.content_id)
        self.assertEqual(len(list((self.base / "artifacts").iterdir())), 1)
        saved = self.destination / "report_2026.bin"
        self.assertEqual(run.outputs, {"saved_path": str(saved)})
        self.assertEqual(saved.read_bytes(), self.source.read_bytes())

    async def test_symlink_collision_does_not_touch_target(self):
        target = self.destination / self.source.name
        target.symlink_to(self.source)
        run = await self.run_workflow()
        self.assertEqual(run.status, WorkflowRunStatus.FAILED)
        self.assertTrue(target.is_symlink())
        self.assertEqual(self.source.read_bytes(), b"\x00\xffexample")

    async def test_missing_source_blocks_save(self):
        self.source.unlink()
        run = await self.run_workflow()
        self.assertEqual(run.status, WorkflowRunStatus.FAILED)
        self.assertEqual(run.nodes[self.save_node.id].status, NodeRunStatus.BLOCKED)
        self.assertEqual(list(self.destination.iterdir()), [])

    async def test_missing_destination_fails_without_creating_directory(self):
        self.destination.rmdir()
        run = await self.run_workflow()
        self.assertEqual(run.status, WorkflowRunStatus.FAILED)
        self.assertFalse(self.destination.exists())

    async def test_save_uses_logical_filename(self):
        ref = await self.store.import_file(self.source)
        renamed = ArtifactRef(content_id=ref.content_id, filename="renamed.bin")
        target = await self.store.save(renamed, self.destination)
        self.assertEqual(target.name, "renamed.bin")
        self.assertEqual(target.read_bytes(), self.source.read_bytes())

    async def test_failed_save_leaves_no_partial_file(self):
        ref = await self.store.import_file(self.source)

        def fail_copy(incoming, outgoing):
            outgoing.write(b"partial")
            raise OSError("write failed")

        with patch("mushin.artifacts.local.shutil.copyfileobj", side_effect=fail_copy):
            with self.assertRaisesRegex(OSError, "write failed"):
                await self.store.save(ref, self.destination)
        self.assertEqual(list(self.destination.iterdir()), [])


if __name__ == "__main__":
    unittest.main()
