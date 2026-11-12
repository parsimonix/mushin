import unittest

from pydantic import BaseModel, TypeAdapter, ValidationError

from mushin.models.registry import TypeRegistry, create_default_type_registry


class Metadata(BaseModel):
    title: str


class TypeRegistryTests(unittest.TestCase):
    def setUp(self):
        self.registry = TypeRegistry()
        self.adapter = TypeAdapter(int)
        self.registry.register("integer", self.adapter)

    def test_lookup_and_duplicate_rejection(self):
        self.assertIs(self.registry.resolve("integer"), self.adapter)
        with self.assertRaises(ValueError):
            self.registry.register("integer", TypeAdapter(str))
        self.assertIs(self.registry.resolve("integer"), self.adapter)

    def test_empty_identifiers(self):
        for identifier in ("", " ", "\t\n"):
            with self.subTest(identifier=identifier), self.assertRaises(ValueError):
                self.registry.register(identifier, self.adapter)

    def test_unknown_types_and_registry_isolation(self):
        with self.assertRaises(KeyError):
            self.registry.resolve("missing")
        with self.assertRaises(KeyError):
            self.registry.validate("missing", 1)
        with self.assertRaises(KeyError):
            TypeRegistry().resolve("integer")

    def test_strict_primitives(self):
        self.assertEqual(self.registry.validate("integer", 42), 42)
        for value in ("42", 42.0, True, None):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                self.registry.validate("integer", value)
        self.registry.register("string", TypeAdapter(str))
        self.registry.register("boolean", TypeAdapter(bool))
        self.assertEqual(self.registry.validate("string", "hello"), "hello")
        self.assertIs(self.registry.validate("boolean", True), True)
        with self.assertRaises(ValidationError):
            self.registry.validate("string", 42)
        with self.assertRaises(ValidationError):
            self.registry.validate("boolean", 1)

    def test_structured_values_and_collections(self):
        self.registry.register("metadata", TypeAdapter(Metadata))
        result = self.registry.validate("metadata", {"title": "Example"})
        self.assertIsInstance(result, Metadata)
        self.assertEqual(result.title, "Example")
        self.registry.register("integers", TypeAdapter(list[int]))
        self.assertEqual(self.registry.validate("integers", [1, 2]), [1, 2])
        with self.assertRaises(ValidationError):
            self.registry.validate("integers", [1, "2"])
        with self.assertRaises(ValidationError):
            self.registry.validate("metadata", {"title": 123})

    def test_default_registry(self):
        registry = create_default_type_registry()
        for type_id, value in (("string", "hello"), ("integer", 3), ("number", 3), ("number", 1.5), ("boolean", True)):
            with self.subTest(type_id=type_id, value=value):
                self.assertEqual(registry.validate(type_id, value), value)
        for type_id, value in (("number", True), ("number", "3"), ("integer", 1.5), ("boolean", 1), ("string", None)):
            with self.subTest(type_id=type_id, value=value), self.assertRaises(ValidationError):
                registry.validate(type_id, value)
        registry.register("metadata", TypeAdapter(Metadata))
        with self.assertRaises(KeyError):
            create_default_type_registry().resolve("metadata")


if __name__ == "__main__":
    unittest.main()
