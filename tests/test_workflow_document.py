import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

from mushin.models.registry import NodeRegistry
from mushin.models.validation import WorkflowValidator
from mushin.models.video_metadata import VideoMetadata
from mushin.nodes import GuessVideo, TemplatedString, to_variables_registration
from mushin.persistence import WorkflowDocumentError, YamlWorkflowRepository, load_workflow_file, parse_workflow_yaml


DOCUMENT = """
name: Authored workflow
nodes:
  guess:
    type: video.guess
  variables:
    type: variables.from.video_metadata
edges:
  - source_node: guess
    source_port: metadata
    target_node: variables
    target_port: value
inputs:
  - name: file
    target_node: guess
    target_port: file
outputs:
  - name: variables
    source_node: variables
    source_port: variables
"""


class WorkflowDocumentTests(unittest.IsolatedAsyncioTestCase):
    async def test_names_resolve_and_generated_ids_are_fresh(self):
        workflow = parse_workflow_yaml(DOCUMENT)
        other = parse_workflow_yaml(DOCUMENT)
        self.assertNotEqual(workflow.id, other.id)
        self.assertNotEqual(workflow.nodes[0].id, other.nodes[0].id)
        self.assertEqual(workflow.nodes[0].name, "guess")
        self.assertEqual(workflow.edges[0].source_node, workflow.nodes[0].id)
        self.assertEqual(workflow.edges[0].target_node, workflow.nodes[1].id)
        self.assertEqual(workflow.inputs[0].target_node, workflow.nodes[0].id)
        self.assertEqual(workflow.outputs[0].source_node, workflow.nodes[1].id)
        registry = NodeRegistry()
        registry.register(GuessVideo.spec, GuessVideo())
        adapter = to_variables_registration(VideoMetadata, input_type="video_metadata")
        registry.register(adapter.spec, adapter.executor)
        self.assertEqual(WorkflowValidator(registry).validate(workflow), [])

    async def test_import_and_save_preserves_ids(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "authored.yaml"
            path.write_text(DOCUMENT)
            workflow = await load_workflow_file(path)
            repository = YamlWorkflowRepository(Path(directory) / "stored")
            await repository.save(workflow)
            self.assertEqual(await repository.load(workflow.id), workflow)
            snapshot = Path(directory, "stored", f"{workflow.id.hex}.yaml")
            self.assertEqual(await load_workflow_file(snapshot), workflow)

    def test_unknown_named_references_and_duplicate_keys_fail(self):
        for document in (
            DOCUMENT.replace("source_node: guess", "source_node: missing"),
            DOCUMENT.replace("target_node: guess", "target_node: missing"),
            DOCUMENT.replace("source_node: variables", "source_node: missing"),
            "name: Duplicate\nnodes:\n  same: {type: first}\n  same: {type: second}\n",
        ):
            with self.subTest(document=document), self.assertRaises(WorkflowDocumentError):
                parse_workflow_yaml(document)

    def test_explicit_ids_and_names_are_preserved(self):
        workflow = parse_workflow_yaml("""
id: 4ae97a4f-4308-4ffe-aaf0-9c70de5db360
name: Explicit identity
nodes:
  template:
    id: b89f5aaa-84b5-4a1b-9eac-231cde1ec3d6
    name: Friendly label
    type: string.template
""")
        self.assertEqual(str(workflow.id), "4ae97a4f-4308-4ffe-aaf0-9c70de5db360")
        self.assertEqual(str(workflow.nodes[0].id), "b89f5aaa-84b5-4a1b-9eac-231cde1ec3d6")
        self.assertEqual(workflow.nodes[0].name, "Friendly label")

    async def test_playground_file_matches_graph_shape(self):
        path = Path(__file__).resolve().parents[1] / "workflows" / "playground.yaml"
        workflow = await load_workflow_file(path)
        self.assertEqual(len(workflow.nodes), 6)
        self.assertEqual(len(workflow.edges), 6)
        template = next(node for node in workflow.nodes if node.type == TemplatedString.spec.type)
        self.assertEqual(template.config["template"], "{title} ({year}).{container}")


if __name__ == "__main__":
    unittest.main()
