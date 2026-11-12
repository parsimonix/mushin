import unittest
from uuid import uuid4

from pydantic import ValidationError

from mushin.models.artifact import ArtifactRef
from mushin.models.registry import create_default_type_registry


class ArtifactRefTests(unittest.TestCase):
    def test_json_round_trip(self):
        ref = ArtifactRef(content_id=uuid4(), filename="Example file.txt")
        self.assertEqual(ArtifactRef.model_validate_json(ref.model_dump_json()), ref)

    def test_immutable_reference_and_shared_content(self):
        original = ArtifactRef(content_id=uuid4(), filename="source.txt")
        renamed = ArtifactRef(content_id=original.content_id, filename="renamed.txt")
        self.assertEqual(original.content_id, renamed.content_id)
        self.assertNotEqual(original, renamed)
        with self.assertRaises(ValidationError):
            original.filename = "changed.txt"

    def test_reject_paths_and_empty_names(self):
        for filename in ("", " ", ".", "..", "../file.txt", "/file.txt", "dir/file.txt", "dir\\file.txt", "C:file.txt", "bad\x00.txt"):
            with self.subTest(filename=filename), self.assertRaises(ValidationError):
                ArtifactRef(content_id=uuid4(), filename=filename)

    def test_registry_validates_artifact_ports(self):
        registry = create_default_type_registry()
        ref = ArtifactRef(content_id=uuid4(), filename="file.txt")
        self.assertEqual(registry.validate("artifact", ref), ref)
        self.assertEqual(registry.validate("artifact", ref.model_dump()), ref)
        for value in ("/tmp/file.txt", None, {"content_id": uuid4(), "filename": "../file.txt"}):
            with self.subTest(value=value), self.assertRaises(ValidationError):
                registry.validate("artifact", value)


if __name__ == "__main__":
    unittest.main()
