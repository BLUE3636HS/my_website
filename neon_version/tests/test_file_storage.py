import asyncio
import importlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

from starlette.exceptions import HTTPException
from file_storage import prepare_upload_directory, require_persistent_file_storage, UploadStaticFiles


class FileStorageTests(unittest.TestCase):
    def test_vercel_import_never_writes_or_connects(self):
        with patch.dict(os.environ, {"VERCEL": "1", "DATABASE_URL": "postgresql://example.invalid/test"}), \
                patch.object(Path, "mkdir", side_effect=AssertionError("read-only filesystem")), \
                patch("psycopg.connect", side_effect=AssertionError("import must not connect")):
            app_module = importlib.import_module("main")
            self.assertTrue(app_module.app.routes)

    def test_local_upload_directory_is_created(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"VERCEL": ""}):
            directory = Path(root) / "uploads" / "profile"
            prepare_upload_directory(directory)
            self.assertTrue(directory.is_dir())
            require_persistent_file_storage()

    def test_vercel_upload_rejected_explicitly(self):
        with patch.dict(os.environ, {"VERCEL": "1"}):
            with self.assertRaises(HTTPException) as error:
                require_persistent_file_storage()
            self.assertEqual(error.exception.status_code, 503)

    def test_missing_upload_folder_returns_404(self):
        with tempfile.TemporaryDirectory() as root:
            files = UploadStaticFiles(Path(root) / "missing")
            asyncio.run(files.check_config())
            with self.assertRaises(HTTPException) as error:
                asyncio.run(files.get_response("missing.webp", {"method": "GET"}))
            self.assertEqual(error.exception.status_code, 404)

    def test_existing_upload_remains_readable_on_vercel(self):
        with tempfile.TemporaryDirectory() as root, patch.dict(os.environ, {"VERCEL": "1"}):
            (Path(root) / "sample.webp").write_bytes(b"existing-file")
            files = UploadStaticFiles(root)
            asyncio.run(files.check_config())
            response = asyncio.run(files.get_response("sample.webp", {"method": "GET", "headers": []}))
            self.assertEqual(response.status_code, 200)
