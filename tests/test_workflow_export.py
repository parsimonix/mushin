import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

import yaml

from mushin.models.node import Edge, Node, Workflow, WorkflowInput, WorkflowOutput
from mushin.persistence import load_workflow_file, parse_workflow_yaml
from mushin.playground import build_workflow


class WorkflowExportTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        source = Node(name="Import Video", type="file.import", config={"text": "café", "list": [1, True, None]})
        target = Node(name="Save Video", type="file.save")
        self.workflow = Workflow(
            name="Export example",
            schema_version=2,
            nodes=[source, target],
            edges=[Edge(source_node=source.id, source_port="file", target_node=target.id, target_port="file")],
            inputs=[WorkflowInput(name="source", target_node=source.id, target_port="source")],
            outputs=[WorkflowOutput(name="path", source_node=target.id, source_port="path")],
        )

    def test_raw_yaml_preserves_complete_snapshot(self):
        before = self.workflow.model_dump_json()
        document = self.workflow.export()
        self.assertEqual(yaml.safe_load(document), self.workflow.model_dump(mode="json"))
        self.assertEqual(parse_workflow_yaml(document), self.workflow)
        self.assertIn("café", document)
        self.assertNotIn("!!python", document)
        self.assertEqual(self.workflow.model_dump_json(), before)

    def test_author_yaml_uses_names_and_translates_all_reference_kinds(self):
        before = self.workflow.model_dump_json()
        document = self.workflow.export(form="author")
        data = yaml.safe_load(document)
        self.assertEqual(list(data["nodes"]), ["import_video", "save_video"])
        self.assertNotIn("id", data)
        self.assertTrue(all("id" not in node for node in data["nodes"].values()))
        self.assertEqual(data["edges"][0]["source_node"], "import_video")
        self.assertEqual(data["edges"][0]["target_node"], "save_video")
        self.assertEqual(data["inputs"][0]["target_node"], "import_video")
        self.assertEqual(data["outputs"][0]["source_node"], "save_video")
        restored = parse_workflow_yaml(document)
        other = parse_workflow_yaml(document)
        self.assertNotEqual(restored.id, self.workflow.id)
        self.assertNotEqual(restored.id, other.id)
        self.assertNotEqual(restored.nodes[0].id, other.nodes[0].id)
        self.assertEqual(restored.nodes[0].config, self.workflow.nodes[0].config)
        self.assertEqual(restored.edges[0].source_node, restored.nodes[0].id)
        self.assertEqual(restored.edges[0].target_node, restored.nodes[1].id)
        self.assertEqual(restored.schema_version, 2)
        self.assertEqual(self.workflow.model_dump_json(), before)
        self.assertEqual(self.workflow.export(form="author"), document)

    def test_author_yaml_can_preserve_ids(self):
        restored = parse_workflow_yaml(self.workflow.export(form="author", include_ids=True))
        self.assertEqual(restored, self.workflow)

    def test_key_generation_handles_duplicates_missing_names_and_punctuation(self):
        workflow = Workflow(
            name="Keys",
            nodes=[
                Node(name="Source", type="example"),
                Node(name="Source", type="example"),
                Node(name="Source_2", type="example"),
                Node(type="example.source"),
                Node(name="!!!", type="example"),
                Node(name="  ", type="example"),
            ],
        )
        data = yaml.safe_load(workflow.export(form="author"))
        self.assertEqual(list(data["nodes"]), ["source", "source_2", "source_2_2", "example_source", "node", "node_2"])
        self.assertNotIn("name", data["nodes"]["example_source"])
        self.assertEqual(parse_workflow_yaml(workflow.export(form="author")).nodes[3].name, "example_source")

    def test_empty_workflow_and_drafts_export_without_registry_validation(self):
        for form in ("raw", "author"):
            document = Workflow(name="Draft").export(form=form)
            self.assertEqual(parse_workflow_yaml(document).nodes, [])
        draft = Workflow(name="Unknown node type", nodes=[Node(type="unregistered")])
        self.assertEqual(len(parse_workflow_yaml(draft.export(form="author")).nodes), 1)

    def test_author_rejects_duplicate_ids_but_raw_retains_draft(self):
        self.workflow.nodes.append(self.workflow.nodes[0].model_copy())
        with self.assertRaisesRegex(ValueError, "duplicate node ID"):
            self.workflow.export(form="author")
        self.assertEqual(parse_workflow_yaml(self.workflow.export()), self.workflow)

    def test_author_rejects_dangling_references_but_raw_retains_draft(self):
        for kind in ("source", "target", "input", "output"):
            draft = self.workflow.model_copy(deep=True)
            if kind == "source":
                draft.edges[0].source_node = uuid4()
            elif kind == "target":
                draft.edges[0].target_node = uuid4()
            elif kind == "input":
                draft.inputs[0].target_node = uuid4()
            else:
                draft.outputs[0].source_node = uuid4()
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, "missing node"):
                draft.export(form="author")
            self.assertEqual(parse_workflow_yaml(draft.export()), draft)

    async def test_file_exports_create_parents_replace_and_reload(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "nested" / "workflow.yaml"
            for form in ("raw", "author"):
                with self.subTest(form=form):
                    document = self.workflow.export(path, form=form, include_ids=True)
                    self.assertEqual(path.read_text(encoding="utf-8"), document)
                    self.assertEqual(await load_workflow_file(path), self.workflow)
                    self.assertEqual(list(path.parent.iterdir()), [path])

    def test_failed_export_preserves_existing_file_and_cleans_staging(self):
        with TemporaryDirectory() as directory:
            path = Path(directory) / "workflow.yaml"
            path.write_text("original", encoding="utf-8")
            with patch("mushin.persistence.workflows.os.replace", side_effect=OSError("replace failed")):
                with self.assertRaisesRegex(OSError, "replace failed"):
                    self.workflow.export(path, form="author")
            self.assertEqual(path.read_text(), "original")
            self.assertEqual(list(path.parent.iterdir()), [path])
            with self.assertRaisesRegex(ValueError, "Unknown workflow export form"):
                self.workflow.export(path, form="invalid")
            self.assertEqual(path.read_text(), "original")

    def test_plex_graph_configuration_survives_author_round_trip(self):
        workflow = build_workflow("variables.from.video_metadata")
        restored = parse_workflow_yaml(workflow.export(form="author", include_ids=True))
        self.assertEqual(restored, workflow)
