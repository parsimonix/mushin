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

    async def run_template(self, template, variables, **options):
        node = Node(id=uuid4(), type=TemplatedString.spec.type, config={"template": template, **options})
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
        for template in ("{", "{}", "{0}", "{title.name}", "{title[0]}", "{title!r}", "{year:04d}",
                         "{season:{width}d}", "{season:1000000d}", "{season:02d!r}"):
            with self.subTest(template=template):
                run = await self.run_template(template, {})
                self.assertEqual(run.error.code, "invalid_workflow")
                self.assertEqual(run.validation_issues[0].code, "invalid_config")

    async def test_two_digit_integer_padding_preserves_larger_values(self):
        for season, episode, expected in ((1, 2, "s01e02"), (0, 9, "s00e09"),
                                          (10, 99, "s10e99"), (100, 123, "s100e123")):
            with self.subTest(season=season, episode=episode):
                run = await self.run_template("s{season:02d}e{episode:02d}", {"season": season, "episode": episode})
                self.assertEqual(run.status, WorkflowRunStatus.SUCCEEDED)
                self.assertEqual(run.outputs, {"text": expected})

    async def test_integer_padding_rejects_noninteger_values(self):
        for value in ("1", 1.5):
            with self.subTest(value=value):
                run = await self.run_template("{season:02d}", {"season": value})
                self.assertEqual(run.status, WorkflowRunStatus.FAILED)
                self.assertEqual(run.outputs, {})

    async def test_missing_variable(self):
        run = await self.run_template("Hello, {name}!", {})
        self.assertEqual(run.status, WorkflowRunStatus.FAILED)
        self.assertIn("Missing template variable 'name'", run.error.message)
        self.assertEqual(run.outputs, {})

    async def test_defaults_fill_missing_values_without_overwriting_supplied_values(self):
        run = await self.run_template("{year}:{season:02d}:{ready}:{label}",
                                      {"season": 0, "ready": False, "label": ""},
                                      defaults={"year": 2024, "season": 1, "ready": True, "label": "fallback"})
        self.assertEqual(run.outputs, {"text": "2024:00:False:"})

    async def test_optional_sections_omit_surrounding_punctuation(self):
        sections = {"year_suffix": " ({year})", "episode_suffix": " - {episode_title}"}
        template = "{title}{year_suffix} - s{season:02d}e{episode:02d}{episode_suffix}.mkv"
        for extra, expected in (
            ({}, "Show - s01e02.mkv"),
            ({"year": 2024}, "Show (2024) - s01e02.mkv"),
            ({"episode_title": "Pilot"}, "Show - s01e02 - Pilot.mkv"),
            ({"year": 2024, "episode_title": "Pilot"}, "Show (2024) - s01e02 - Pilot.mkv"),
            ({"episode_title": ""}, "Show - s01e02.mkv"),
        ):
            with self.subTest(extra=extra):
                run = await self.run_template(template, {"title": "Show", "season": 1, "episode": 2, **extra},
                                              optional_sections=sections)
                self.assertEqual(run.outputs, {"text": expected})

    async def test_optional_sections_require_all_fields_and_preserve_zero_false(self):
        run = await self.run_template("{details}", {"season": 0, "ready": False},
                                      optional_sections={"details": "{season:02d}/{ready}"})
        self.assertEqual(run.outputs, {"text": "00/False"})
        run = await self.run_template("{details}", {"season": 0},
                                      optional_sections={"details": "{season:02d}/{ready}"})
        self.assertEqual(run.outputs, {"text": ""})
        run = await self.run_template("{details}", {}, defaults={"season": 1},
                                      optional_sections={"details": "S{season:02d}"})
        self.assertEqual(run.outputs, {"text": "S01"})

    async def test_optional_sections_do_not_hide_other_missing_fields(self):
        run = await self.run_template("{title}{suffix}", {}, optional_sections={"suffix": " ({year})"})
        self.assertIn("Missing template variable 'title'", run.error.message)

    async def test_invalid_optional_sections_and_defaults_fail_validation(self):
        for options in (
            {"optional_sections": {"invalid.name": "text"}},
            {"optional_sections": {"suffix": "{value.name}"}},
            {"optional_sections": {"suffix": "{year:1000000d}"}},
            {"optional_sections": {"suffix": "{suffix}"}},
            {"optional_sections": {"first": "{second}", "second": "text"}},
            {"optional_sections": {"suffix": "text"}, "defaults": {"suffix": "collision"}},
            {"defaults": {"year": None}},
            {"defaults": {"year": {"nested": "value"}}},
        ):
            with self.subTest(options=options):
                run = await self.run_template("{suffix}", {}, **options)
                self.assertEqual(run.error.code, "invalid_workflow")

    async def test_section_names_cannot_overwrite_inputs(self):
        run = await self.run_template("{suffix}", {"suffix": "existing"}, optional_sections={"suffix": "text"})
        self.assertIn("cannot use optional section names", run.error.message)

    async def test_variables_reject_nested_values_and_null(self):
        for value in (None, [], {"nested": "value"}):
            with self.subTest(value=value):
                run = await self.run_template("{title}", {"title": value})
                self.assertEqual(run.error.code, "invalid_workflow_input")


if __name__ == "__main__":
    unittest.main()
