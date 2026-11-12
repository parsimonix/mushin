import unittest
from uuid import uuid4

from mushin.models.artifact import ArtifactRef
from mushin.models.node import Node, Workflow, WorkflowInput, WorkflowOutput
from mushin.models.registry import NodeRegistry
from mushin.models.run import WorkflowRunner, WorkflowRunStatus
from mushin.nodes import RenameFile


class RenameFileTests(unittest.IsolatedAsyncioTestCase):
    async def test_rename_and_invalid_filenames(self):
        registry = NodeRegistry()
        registry.register(RenameFile.spec, RenameFile())
        runner = WorkflowRunner(registry)
        node = Node(id=uuid4(), type=RenameFile.spec.type)
        workflow = Workflow(
            id=uuid4(), name="Rename reference", nodes=[node],
            inputs=[
                WorkflowInput(name="file", target_node=node.id, target_port="file"),
                WorkflowInput(name="filename", target_node=node.id, target_port="filename"),
            ],
            outputs=[WorkflowOutput(name="file", source_node=node.id, source_port="file")],
        )
        # Renaming is independent of storage: this content ID need not exist yet.
        original = ArtifactRef(content_id=uuid4(), filename="original.txt")
        run = await runner.run(workflow, inputs={"file": original, "filename": "renamed.txt"})
        self.assertEqual(run.status, WorkflowRunStatus.SUCCEEDED)
        self.assertEqual(run.outputs["file"], ArtifactRef(content_id=original.content_id, filename="renamed.txt"))
        self.assertEqual(original.filename, "original.txt")
        for filename in ("", "..", "../escape.txt", "/absolute.txt", "dir\\file.txt", "bad\x00.txt"):
            with self.subTest(filename=filename):
                run = await runner.run(workflow, inputs={"file": original, "filename": filename})
                self.assertEqual(run.status, WorkflowRunStatus.FAILED)
                self.assertEqual(run.error.node_id, node.id)
                self.assertEqual(run.outputs, {})
                self.assertEqual(original.filename, "original.txt")


if __name__ == "__main__":
    unittest.main()
