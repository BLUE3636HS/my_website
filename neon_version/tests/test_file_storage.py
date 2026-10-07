import os
import unittest
from unittest.mock import MagicMock, patch

from fastapi import HTTPException
from file_storage import (delete, download, object_key,
                          require_storage, upload, valid_key)


class FileStorageTests(unittest.TestCase):
    def test_keys_are_scoped_and_safe(self):
        key = object_key("study-images", "student/..", "image.jpg")
        self.assertRegex(key, r"^study-images/student_../[a-f0-9]{32}\.jpg$")
        self.assertTrue(valid_key(key, "study-images", {"jpg", "png"}))
        self.assertFalse(valid_key("study-images/../secret.jpg", "study-images", {"jpg"}))
        self.assertFalse(valid_key("study-images/1/../secret.jpg", "study-images", {"jpg"}))

    def test_missing_environment_is_clear(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaises(HTTPException) as http_error:
                require_storage()
            self.assertEqual(http_error.exception.status_code, 503)
            self.assertIn("AWS_ENDPOINT_URL_S3", str(http_error.exception.detail))

    def test_upload_download_delete_use_private_bucket_and_content_type(self):
        client = MagicMock()
        client.get_object.return_value = {"Body": MagicMock(read=lambda: b"payload")}
        settings = {"AWS_ENDPOINT_URL_S3": "https://storage.example", "AWS_ACCESS_KEY_ID": "key",
                    "AWS_SECRET_ACCESS_KEY": "secret", "AWS_REGION": "region", "STORAGE_BUCKET": "uploads"}
        with patch.dict(os.environ, settings), patch("file_storage._client", return_value=client):
            upload("profile/1/" + "a" * 32 + ".webp", b"image", "image/webp")
            self.assertEqual(download("profile/1/" + "a" * 32 + ".webp"), b"payload")
            delete("profile/1/" + "a" * 32 + ".webp")
        client.put_object.assert_called_once_with(Bucket="uploads", Key="profile/1/" + "a" * 32 + ".webp",
                                                  Body=b"image", ContentType="image/webp")
        client.delete_object.assert_called_once()

    def test_storage_error_hides_client_details(self):
        client = MagicMock()
        client.put_object.side_effect = RuntimeError("https://private.example?secret=bad")
        settings = {"AWS_ENDPOINT_URL_S3": "https://storage.example", "AWS_ACCESS_KEY_ID": "key",
                    "AWS_SECRET_ACCESS_KEY": "secret", "AWS_REGION": "region", "STORAGE_BUCKET": "uploads"}
        with patch.dict(os.environ, settings), patch("file_storage._client", return_value=client):
            with self.assertRaises(Exception) as error:
                upload("profile/1/" + "a" * 32 + ".webp", b"image", "image/webp")
        self.assertNotIn("private.example", str(error.exception))

    def test_failed_upload_uses_safe_error_message(self):
        client = MagicMock()
        client.put_object.side_effect = RuntimeError("credential=secret")
        settings = {"AWS_ENDPOINT_URL_S3": "https://storage.example", "AWS_ACCESS_KEY_ID": "key",
                    "AWS_SECRET_ACCESS_KEY": "secret", "AWS_REGION": "region", "STORAGE_BUCKET": "uploads"}
        with patch.dict(os.environ, settings), patch("file_storage._client", return_value=client):
            with self.assertRaises(Exception) as error:
                upload("profile/1/" + "a" * 32 + ".webp", b"image", "image/webp")
        self.assertNotIn("credential", str(error.exception))

    def test_private_download_requires_database_registered_valid_key(self):
        key = object_key("profile", "student-1", "image.webp")
        self.assertTrue(valid_key(key, "profile", {"webp"}))
        self.assertFalse(valid_key("profile/student-1/../../secret.webp", "profile", {"webp"}))


if __name__ == "__main__":
    unittest.main()
