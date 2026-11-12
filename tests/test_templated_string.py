import unittest
from uuid import uuid4

from mushin.models.node import Node, Workflow, WorkflowInput, WorkflowOutput
from mushin.models.registry import NodeRegistry
from mushin.models.run import WorkflowRunner, WorkflowRunStatus
from mushin.nodes import TemplatedString


class TemplatedStringTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        registry = NodeRegistry()
        registry.register(TemplatedString.spec, TemplatedString())
        self.runner = WorkflowRunner(registry)

    async def run_template(self, template, variables):
        node = Node(id=uuid4(), type=TemplatedString.spec.type, config={"template": template})
        workflow = Workflow(
            id=uuid4(), name="Render a string", nodes=[node],
            inputs=[WorkflowInput(name="variables", target_node=node.id, target_port="variables")],
            outputs=[WorkflowOutput(name="text", source_node=node.id, source_port="text")],
        )
        return await self.runner.run(workflow, inputs={"variables": variables})

    async def test_interpolation_and_literal_braces(self):
        for template, variables, expected in (
            ("{title}_{year}{extension}", {"title": "report", "year": 2026, "extension": ".pdf"}, "report_2026.pdf"),
            ("{{{title}}}_{title}.txt", {"title": "report", "unused": 2}, "{report}_report.txt"),
            ("fixed.txt", {}, "fixed.txt"),
            ("Hello, {name}!\nReady: {ready}", {"name": "Jon", "ready": True}, "Hello, Jon!\nReady: True"),
            ("{value}", {"value": "../folder/file.txt"}, "../folder/file.txt"),
            ("{value}", {"value": ""}, ""),
            ("", {}, ""),
        ):
            with self.subTest(template=template):
                run = await self.run_template(template, variables)
                self.assertEqual(run.status, WorkflowRunStatus.SUCCEEDED)
                self.assertEqual(run.outputs, {"text": expected})

    async def test_bad_template_fails_validation(self):
        for template in ("{", "{}", "{0}", "{title.name}", "{title[0]}", "{title!r}", "{year:04d}"):
            with self.subTest(template=template):
                run = await self.run_template(template, {})
                self.assertEqual(run.error.code, "invalid_workflow")
                self.assertEqual(run.validation_issues[0].code, "invalid_config")

    async def test_missing_variable(self):
        run = await self.run_template("Hello, {name}!", {})
        self.assertEqual(run.status, WorkflowRunStatus.FAILED)
        self.assertIn("Missing template variable 'name'", run.error.message)
        self.assertEqual(run.outputs, {})

    async def test_variables_reject_nested_values_and_null(self):
        for value in (None, [], {"nested": "value"}):
            with self.subTest(value=value):
                run = await self.run_template("{title}", {"title": value})
                self.assertEqual(run.error.code, "invalid_workflow_input")


if __name__ == "__main__":
    unittest.main()
