import os
import unittest
from unittest.mock import MagicMock, patch

import create_admin


class CreateAdminTests(unittest.TestCase):
    def test_missing_url_is_prompted_without_echo_and_account_is_inserted(self):
        connection = MagicMock()
        connection.__enter__.return_value = connection
        connection.execute.return_value.fetchone.return_value = None
        with (patch.dict(os.environ, {"DATABASE_URL": ""}),
              patch("builtins.input", return_value="admin-test"),
              patch.object(create_admin.getpass, "getpass",
                           side_effect=["postgresql://example.invalid/test", "long-password-123"]) as prompt,
              patch.object(create_admin.dbapi, "connect", return_value=connection),
              patch("builtins.print")):
            self.assertEqual(create_admin.main(), 0)
            self.assertEqual(os.environ["DATABASE_URL"], "postgresql://example.invalid/test")
            self.assertEqual(prompt.call_args_list[0].args, ("Neon DATABASE_URL: ",))
            self.assertEqual(connection.execute.call_count, 2)

    def test_empty_url_stops_before_admin_creation(self):
        with (patch.dict(os.environ, {"DATABASE_URL": ""}),
              patch.object(create_admin.getpass, "getpass", return_value=""),
              patch.object(create_admin.dbapi, "connect") as connect,
              patch("builtins.print")):
            self.assertEqual(create_admin.main(), 1)
            connect.assert_not_called()


if __name__ == "__main__":
    unittest.main()
