import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch
from uuid import uuid4

from pydantic import ValidationError

from mushin.artifacts import LocalArtifactStore
from mushin.models.artifact import ArtifactRef


class LocalArtifactStoreTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.workspace = TemporaryDirectory()
        self.addCleanup(self.workspace.cleanup)
        self.base = Path(self.workspace.name)
        self.root = self.base / "artifacts"
        self.store = LocalArtifactStore(self.root)

    async def test_import_is_independent_of_source(self):
        source = self.base / "source.txt"
        source.write_bytes(b"original")
        ref = await self.store.import_file(source)
        self.assertEqual(ref.filename, "source.txt")
        self.assertEqual(source.read_bytes(), b"original")
        source.write_bytes(b"changed")
        self.assertEqual(await self.store.read(ref), b"original")
        source.unlink()
        self.assertEqual(await self.store.read(ref), b"original")

    async def test_create_rename_and_reopen(self):
        ref = await self.store.create(b"\x00\xffcontent", filename="original.bin")
        renamed = ArtifactRef(content_id=ref.content_id, filename="renamed.bin")
        restored = ArtifactRef.model_validate_json(renamed.model_dump_json())
        self.assertEqual(await LocalArtifactStore(self.root).read(restored), b"\x00\xffcontent")
        self.assertEqual(len(list(self.root.iterdir())), 1)
        other = await self.store.create(b"new", filename="original.bin")
        self.assertNotEqual(other.content_id, ref.content_id)
        self.assertEqual(await self.store.read(ref), b"\x00\xffcontent")
        self.assertEqual(await self.store.read(other), b"new")

    async def test_missing_content_and_source(self):
        ref = ArtifactRef(content_id=uuid4(), filename="missing.txt")
        with self.assertRaises(FileNotFoundError):
            await self.store.read(ref)
        with self.assertRaises(FileNotFoundError):
            await self.store.import_file(self.base / "missing.txt")
        self.assertFalse(self.root.exists())

    async def test_invalid_filename_does_not_write(self):
        with self.assertRaises(ValidationError):
            await self.store.create(b"content", filename="../escape.txt")
        self.assertFalse(self.root.exists())

    async def test_failed_copy_cleans_staging_content(self):
        source = self.base / "source.txt"
        source.write_bytes(b"original")

        def fail_copy(incoming, outgoing):
            outgoing.write(b"partial")
            raise OSError("copy failed")

        with patch("mushin.artifacts.local.shutil.copyfileobj", side_effect=fail_copy):
            with self.assertRaisesRegex(OSError, "copy failed"):
                await self.store.import_file(source)
        self.assertEqual(list(self.root.iterdir()), [])
        self.assertEqual(source.read_bytes(), b"original")

    async def test_publication_does_not_overwrite_existing_content(self):
        ref = await self.store.create(b"original", filename="file.txt")
        with patch("mushin.artifacts.local.uuid4", return_value=ref.content_id):
            with self.assertRaises(FileExistsError):
                await self.store.create(b"replacement", filename="other.txt")
        self.assertEqual(await self.store.read(ref), b"original")
        self.assertEqual(len(list(self.root.iterdir())), 1)


if __name__ == "__main__":
    unittest.main()
