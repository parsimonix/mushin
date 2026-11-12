import unittest
from uuid import uuid4

from pydantic import BaseModel, TypeAdapter

from mushin.models.node import Node, Workflow, WorkflowInput, WorkflowOutput
from mushin.models.registry import NodeRegistry, create_default_type_registry
from mushin.models.run import WorkflowRunner, WorkflowRunStatus
from mushin.nodes import to_variables_registration


class Movie(BaseModel):
    title: str
    year: int
    rating: float
    watched: bool


class Nested(BaseModel):
    movie: Movie


class OptionalTitle(BaseModel):
    title: str | None = None


class ToVariablesTests(unittest.IsolatedAsyncioTestCase):
    async def convert(self, model, value, config=None):
        types = create_default_type_registry()
        types.register("example", TypeAdapter(model))
        registration = to_variables_registration(model, input_type="example")
        registry = NodeRegistry()
        registry.register(registration.spec, registration.executor)
        node = Node(id=uuid4(), type=registration.spec.type, config=config or {})
        workflow = Workflow(
            id=uuid4(), name="Convert model", nodes=[node],
            inputs=[WorkflowInput(name="value", target_node=node.id, target_port="value")],
            outputs=[WorkflowOutput(name="variables", source_node=node.id, source_port="variables")],
        )
        return await WorkflowRunner(registry, types).run(workflow, inputs={"value": value})

    async def test_custom_model_scalars(self):
        values = {"title": "Example", "year": 2026, "rating": 4.5, "watched": False}
        run = await self.convert(Movie, values)
        self.assertEqual(run.status, WorkflowRunStatus.SUCCEEDED)
        self.assertEqual(run.outputs, {"variables": values})

    async def test_nested_and_null_fields_fail_explicitly(self):
        for model, value, field in (
            (Nested, {"movie": {"title": "Example", "year": 2026, "rating": 4.5, "watched": True}}, "movie"),
            (OptionalTitle, {}, "title"),
        ):
            with self.subTest(model=model):
                run = await self.convert(model, value)
                self.assertEqual(run.status, WorkflowRunStatus.FAILED)
                self.assertIn("unsupported fields", run.error.message)
                self.assertIn(field, run.error.message)
                self.assertEqual(run.outputs, {})

    def test_factory_preserves_stable_ids_and_port_types(self):
        registration = to_variables_registration(Movie, input_type="movie")
        self.assertEqual(registration.spec.type, "variables.from.movie")
        self.assertEqual(registration.spec.inputs[0].type, "movie")
        self.assertEqual(registration.spec.outputs[0].type, "template_variables")
        custom = to_variables_registration(Movie, input_type="movie", node_type="movie.variables", version=2)
        self.assertEqual((custom.spec.type, custom.spec.version), ("movie.variables", 2))

    async def test_field_selection_and_unknown_names(self):
        value = {"title": "Example", "year": 2026, "rating": 0.0, "watched": False}
        run = await self.convert(Movie, value, {"fields": ["title", "watched", "rating"]})
        self.assertEqual(run.outputs["variables"], {"title": "Example", "watched": False, "rating": 0.0})
        run = await self.convert(Movie, value, {"fields": ["typo"]})
        self.assertIn("Unknown variable fields", run.error.message)
        run = await self.convert(Movie, value, {"fields": []})
        self.assertEqual(run.outputs["variables"], {})

    async def test_null_omission_and_list_policy(self):
        class Details(BaseModel):
            title: str | None = None
            episodes: list[int] = [2, 3]

        run = await self.convert(Details, {}, {"omit_none": True, "list_separator": "-"})
        self.assertEqual(run.outputs["variables"], {"episodes": "2-3"})
        run = await self.convert(Details, {}, {"omit_none": True})
        self.assertIn("unsupported fields", run.error.message)
        run = await self.convert(Details, {"episodes": []}, {"omit_none": True, "list_separator": "-"})
        self.assertEqual(run.outputs["variables"], {"episodes": ""})

    async def test_join_does_not_stringify_nested_objects(self):
        class Details(BaseModel):
            items: list[dict[str, str]]

        run = await self.convert(Details, {"items": [{"key": "value"}]}, {"list_separator": ","})
        self.assertIn("unsupported fields", run.error.message)


if __name__ == "__main__":
    unittest.main()
