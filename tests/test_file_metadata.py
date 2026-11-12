import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from uuid import uuid4

from mushin.artifacts import LocalArtifactStore
from mushin.models.artifact import ArtifactRef
from mushin.models.node import Node, Workflow, WorkflowInput, WorkflowOutput
from mushin.models.registry import NodeRegistry
from mushin.models.run import WorkflowRunner, WorkflowRunStatus
from mushin.nodes import InspectFile
from mushin.types.file_metadata import FileMetadata


class FileMetadataTests(unittest.IsolatedAsyncioTestCase):
    async def test_metadata_through_workflow(self):
        with TemporaryDirectory() as workspace:
            store = LocalArtifactStore(Path(workspace) / "artifacts")
            registry = NodeRegistry()
            registry.register(InspectFile.spec, InspectFile(store))
            node = Node(id=uuid4(), type=InspectFile.spec.type)
            workflow = Workflow(
                id=uuid4(),
                name="Inspect artifact",
                nodes=[node],
                inputs=[WorkflowInput(name="file", target_node=node.id, target_port="file")],
                outputs=[WorkflowOutput(name="metadata", source_node=node.id, source_port="metadata")],
            )
            runner = WorkflowRunner(registry)
            for filename, content, stem, extension in (
                ("report.txt", b"hello", "report", ".txt"),
                ("archive.tar.gz", b"\x00\xff", "archive.tar", ".gz"),
                ("README", b"", "README", ""),
                (".hidden", b"x", ".hidden", ""),
            ):
                with self.subTest(filename=filename):
                    ref = await store.create(content, filename=filename)
                    before = set(Path(workspace, "artifacts").iterdir())
                    run = await runner.run(workflow, inputs={"file": ref})
                    self.assertEqual(run.status, WorkflowRunStatus.SUCCEEDED)
                    metadata = run.outputs["metadata"]
                    self.assertIsInstance(metadata, FileMetadata)
                    self.assertEqual(
                        metadata.model_dump(),
                        {
                            "filename": filename,
                            "stem": stem,
                            "extension": extension,
                            "size_bytes": len(content),
                        },
                    )
                    self.assertEqual(FileMetadata.model_validate_json(metadata.model_dump_json()), metadata)
                    self.assertEqual(await store.read(ref), content)
                    self.assertEqual(set(Path(workspace, "artifacts").iterdir()), before)
                    renamed = ArtifactRef(content_id=ref.content_id, filename="renamed.bin")
                    renamed_run = await runner.run(workflow, inputs={"file": renamed})
                    self.assertEqual(renamed_run.outputs["metadata"].filename, "renamed.bin")
                    self.assertEqual(renamed_run.outputs["metadata"].size_bytes, len(content))

            missing = ArtifactRef(content_id=uuid4(), filename="missing.txt")
            run = await runner.run(workflow, inputs={"file": missing})
            self.assertEqual(run.status, WorkflowRunStatus.FAILED)
            self.assertEqual(run.error.node_id, node.id)
            self.assertIn("FileNotFoundError", run.error.message)
            self.assertEqual(run.outputs, {})


if __name__ == "__main__":
    unittest.main()
