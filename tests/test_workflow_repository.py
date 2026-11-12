import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

import yaml

from mushin.models.node import Edge, Node, Workflow, WorkflowInput, WorkflowOutput
from mushin.persistence import WorkflowDocumentError, WorkflowRepository, YamlWorkflowRepository


class YamlWorkflowRepositoryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        workspace = TemporaryDirectory()
        self.addCleanup(workspace.cleanup)
        self.root = Path(workspace.name) / "workflows"
        self.repository: WorkflowRepository = YamlWorkflowRepository(self.root)

    def path(self, workflow):
        return self.root / f"{workflow.id.hex}.yaml"

    async def test_round_trip_across_repository_instances(self):
        source = Node(name="Source", type="example.source", config={"text": "café", "nullable": None, "items": [1, True]})
        target = Node(name="Target", type="example.target", config={"template": "{title} ({year})"})
        workflow = Workflow(
            name="Readable workflow", nodes=[source, target],
            edges=[Edge(source_node=source.id, source_port="out", target_node=target.id, target_port="in")],
            inputs=[WorkflowInput(name="title", target_node=source.id, target_port="title")],
            outputs=[WorkflowOutput(name="result", source_node=target.id, source_port="out")],
        )
        before = workflow.model_dump_json()
        await self.repository.save(workflow)
        restored = await YamlWorkflowRepository(self.root).load(workflow.id)
        self.assertEqual(restored, workflow)
        self.assertEqual(workflow.model_dump_json(), before)
        text = self.path(workflow).read_text(encoding="utf-8")
        self.assertIn("name: Readable workflow", text)
        self.assertIn("café", text)
        self.assertNotIn("!!python", text)
        self.assertEqual(yaml.safe_load(text), workflow.model_dump(mode="json"))

    async def test_incomplete_drafts_and_replacement(self):
        # Unknown node types and dangling connections are allowed in drafts.
        workflow = Workflow(name="Draft", nodes=[Node(type="unknown")], edges=[
            Edge(source_node=uuid4(), source_port="out", target_node=uuid4(), target_port="in"),
        ])
        await self.repository.save(workflow)
        self.assertEqual(await self.repository.load(workflow.id), workflow)
        workflow.name = "Revised draft"
        await self.repository.save(workflow)
        self.assertEqual((await self.repository.load(workflow.id)).name, "Revised draft")
        self.assertEqual(list(self.root.iterdir()), [self.path(workflow)])

    async def test_list_delete_and_missing_documents(self):
        self.assertEqual(await self.repository.list(), [])
        workflows = [Workflow(name="One"), Workflow(name="Two")]
        for workflow in workflows:
            await self.repository.save(workflow)
        (self.root / "notes.yaml").write_text("notes: unrelated\n")
        (self.root / ".pending-example").write_text("partial")
        self.assertEqual(await self.repository.list(), sorted(workflows, key=lambda workflow: workflow.id.hex))
        await self.repository.delete(workflows[0].id)
        self.assertEqual(await self.repository.list(), [workflows[1]])
        with self.assertRaises(FileNotFoundError):
            await self.repository.load(workflows[0].id)
        with self.assertRaises(FileNotFoundError):
            await self.repository.delete(workflows[0].id)

    async def test_invalid_documents(self):
        workflow = Workflow(name="Example")
        await self.repository.save(workflow)
        for text in (
            "name: [broken",
            "[]",
            "name: Example\n",  # A stored document must retain its ID.
            f"id: {workflow.id}\nname: One\nname: Two\n",
            "!!python/object/apply:builtins.str [unsafe]\n",
            f"id: {uuid4()}\nname: Mismatched ID\n",
        ):
            with self.subTest(text=text):
                self.path(workflow).write_text(text)
                with self.assertRaises(WorkflowDocumentError):
                    await self.repository.load(workflow.id)
                with self.assertRaises(WorkflowDocumentError):
                    await self.repository.list()

    async def test_failed_replace_preserves_previous_document_and_cleans_staging(self):
        workflow = Workflow(name="Original")
        await self.repository.save(workflow)
        workflow.name = "Changed"
        with patch("mushin.persistence.workflows.os.replace", side_effect=OSError("replace failed")):
            with self.assertRaisesRegex(OSError, "replace failed"):
                await self.repository.save(workflow)
        self.assertEqual((await self.repository.load(workflow.id)).name, "Original")
        self.assertEqual(list(self.root.iterdir()), [self.path(workflow)])


if __name__ == "__main__":
    unittest.main()
