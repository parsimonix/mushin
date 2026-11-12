import unittest
from datetime import date
from unittest.mock import patch
from uuid import uuid4

from mushin.models.artifact import ArtifactRef
from mushin.models.node import Edge, Node, Workflow, WorkflowInput, WorkflowOutput
from mushin.models.registry import NodeRegistry
from mushin.models.run import WorkflowRunner, WorkflowRunStatus
from mushin.models.video_metadata import VideoMetadata
from mushin.nodes import GuessVideo, TemplatedString, to_variables_registration


class GuessVideoTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        registry = NodeRegistry()
        registry.register(GuessVideo.spec, GuessVideo())
        self.runner = WorkflowRunner(registry)

    async def guess(self, filename, config=None):
        node = Node(type=GuessVideo.spec.type, config=config or {})
        workflow = Workflow(
            name="Guess video metadata", nodes=[node],
            inputs=[WorkflowInput(name="file", target_node=node.id, target_port="file")],
            outputs=[WorkflowOutput(name="metadata", source_node=node.id, source_port="metadata")],
        )
        ref = ArtifactRef(content_id=uuid4(), filename=filename)
        return await self.runner.run(workflow, inputs={"file": ref})

    async def test_movie_metadata_and_json_round_trip(self):
        run = await self.guess("The.Matrix.1999.1080p.BluRay.x264.mkv")
        self.assertEqual(run.status, WorkflowRunStatus.SUCCEEDED)
        metadata = run.outputs["metadata"]
        self.assertIsInstance(metadata, VideoMetadata)
        self.assertEqual((metadata.title, metadata.year, metadata.type, metadata.container), ("The Matrix", 1999, "movie", "mkv"))
        self.assertEqual(metadata.model_dump()["screen_size"], "1080p")
        self.assertEqual(VideoMetadata.model_validate_json(metadata.model_dump_json()), metadata)

    async def test_multiple_episodes_are_preserved(self):
        run = await self.guess("Example.Show.S01E02E03.1080p.WEB-DL.x264-GROUP.mkv")
        self.assertEqual(run.status, WorkflowRunStatus.SUCCEEDED)
        metadata = run.outputs["metadata"]
        self.assertEqual((metadata.title, metadata.type, metadata.season, metadata.episode), ("Example Show", "episode", 1, [2, 3]))
        self.assertIsNone(metadata.year)

    async def test_options_and_non_json_values(self):
        with patch("mushin.nodes.guess_video.guessit", return_value={"title": "News", "date": date(2026, 11, 11)}) as parse:
            run = await self.guess("News.mkv", {"type": "episode"})
        parse.assert_called_once_with("News.mkv", {"no_user_config": True, "type": "episode"})
        self.assertEqual(run.outputs["metadata"].model_dump()["date"], "2026-11-11")

    async def test_parser_failure_is_a_structured_run_error(self):
        with patch("mushin.nodes.guess_video.guessit", side_effect=RuntimeError("parse failed")):
            run = await self.guess("example.mkv")
        self.assertEqual(run.status, WorkflowRunStatus.FAILED)
        self.assertIn("parse failed", run.error.message)
        self.assertEqual(run.outputs, {})

    async def test_invalid_configuration_never_calls_parser(self):
        with patch("mushin.nodes.guess_video.guessit") as parse:
            run = await self.guess("example.mkv", {"type": "invalid"})
        self.assertEqual(run.error.code, "invalid_workflow")
        parse.assert_not_called()

    async def test_guessed_metadata_to_templated_string(self):
        registry = NodeRegistry()
        registry.register(GuessVideo.spec, GuessVideo())
        adapter = to_variables_registration(VideoMetadata, input_type="video_metadata")
        registry.register(adapter.spec, adapter.executor)
        registry.register(TemplatedString.spec, TemplatedString())
        for filename, fields, template, expected in (
            ("The.Matrix.1999.1080p.BluRay.x264.mkv", ["title", "year", "container"], "{title} ({year}).{container}", "The Matrix (1999).mkv"),
            ("Example.Show.S01E02E03.mkv", ["title", "season", "episode", "container"], "{title}_S{season}_E{episode}.{container}", "Example Show_S1_E2-3.mkv"),
        ):
            with self.subTest(filename=filename):
                guess = Node(type=GuessVideo.spec.type)
                variables = Node(type=adapter.spec.type, config={"fields": fields, "omit_none": True, "list_separator": "-"})
                render = Node(type=TemplatedString.spec.type, config={"template": template})
                workflow = Workflow(
                    name="Name video", nodes=[guess, variables, render],
                    edges=[
                        Edge(source_node=guess.id, source_port="metadata", target_node=variables.id, target_port="value"),
                        Edge(source_node=variables.id, source_port="variables", target_node=render.id, target_port="variables"),
                    ],
                    inputs=[WorkflowInput(name="file", target_node=guess.id, target_port="file")],
                    outputs=[WorkflowOutput(name="text", source_node=render.id, source_port="text")],
                )
                workflow = Workflow.model_validate_json(workflow.model_dump_json())
                run = await WorkflowRunner(registry).run(workflow, inputs={"file": ArtifactRef(content_id=uuid4(), filename=filename)})
                self.assertEqual(run.status, WorkflowRunStatus.SUCCEEDED)
                self.assertEqual(run.outputs, {"text": expected})
                # Omitted unknown metadata still produces a clear missing-variable error.
                workflow.nodes[-1].config["template"] = "{episode_title}"
                run = await WorkflowRunner(registry).run(workflow, inputs={"file": ArtifactRef(content_id=uuid4(), filename=filename)})
                self.assertEqual(run.status, WorkflowRunStatus.FAILED)
                self.assertIn("Missing template variable 'episode_title'", run.error.message)


if __name__ == "__main__":
    unittest.main()
