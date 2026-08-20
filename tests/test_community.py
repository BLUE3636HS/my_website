import asyncio
import gc
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

import main


SCHEMA = """
CREATE TABLE community_post (
    id INTEGER PRIMARY KEY AUTOINCREMENT, user_id TEXT NOT NULL,
    parent_id INTEGER, content TEXT NOT NULL, created_at TEXT NOT NULL,
    deleted_at TEXT
);
CREATE TABLE community_like (
    id INTEGER PRIMARY KEY AUTOINCREMENT, post_id INTEGER NOT NULL,
    user_id TEXT NOT NULL, created_at TEXT NOT NULL,
    UNIQUE(post_id, user_id)
);
"""


class FakeRequest:
    def __init__(self, session):
        self.session = session


def response_json(response):
    return json.loads(response.body)


class CommunityRouteTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp.name) / "community.db"
        with closing(sqlite3.connect(self.db_path)) as db:
            db.executescript(SCHEMA)
        self.student = FakeRequest({
            "user_login": True,
            "user_id": "student-a",
            "community_csrf_token": "token"
        })
        self.other = FakeRequest({
            "user_login": True,
            "user_id": "student-b",
            "community_csrf_token": "token-b"
        })
        self.admin = FakeRequest({
            "admin_login": True,
            "admin_id": "admin",
            "community_csrf_token": "admin-token"
        })
        self.path_patch = patch.object(main, "DATABASE_PATH", self.db_path)
        self.path_patch.start()

    def tearDown(self):
        self.path_patch.stop()
        gc.collect()
        self.temp.cleanup()

    def await_result(self, awaitable):
        return asyncio.run(awaitable)

    def create(self, request, content, parent_id=None, token="token"):
        return self.await_result(main.CreateCommunityPost(request, content, parent_id, token))

    def test_post_reply_like_delete_and_permissions(self):
        created = self.create(self.student, "1行目\n2行目")
        self.assertEqual(created.status_code, 201)
        root_id = response_json(created)["post"]["id"]
        reply = self.create(self.other, "返信", root_id, "token-b")
        self.assertEqual(reply.status_code, 201)
        reply_id = response_json(reply)["post"]["id"]
        nested = self.create(self.student, "返信への返信", reply_id)
        self.assertEqual(nested.status_code, 201)

        liked = self.await_result(main.ToggleCommunityLike(self.student, root_id, "token"))
        self.assertEqual(response_json(liked), {"liked": True, "like_count": 1})
        unliked = self.await_result(main.ToggleCommunityLike(self.student, root_id, "token"))
        self.assertEqual(response_json(unliked), {"liked": False, "like_count": 0})
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM community_like").fetchone()[0], 0)

        forbidden = self.await_result(main.DeleteCommunityPost(self.other, root_id, "token-b"))
        self.assertEqual(forbidden.status_code, 403)
        deleted = self.await_result(main.DeleteCommunityPost(self.student, root_id, "token"))
        self.assertEqual(deleted.status_code, 200)
        posts = response_json(self.await_result(main.CommunityPosts(self.student)))
        self.assertTrue(posts["posts"][0]["is_deleted"])
        self.assertEqual(len(posts["posts"][0]["replies"]), 1)
        self.assertEqual(len(posts["posts"][0]["replies"][0]["replies"]), 1)

    def test_validation_missing_parent_deleted_parent_and_admin_delete(self):
        self.assertEqual(self.create(self.student, "   ").status_code, 422)
        self.assertEqual(self.create(self.student, "x" * 200).status_code, 201)
        self.assertEqual(self.create(self.student, "x" * 201).status_code, 422)
        self.assertEqual(self.create(self.student, "reply", 999).status_code, 404)
        anonymous = FakeRequest({"community_csrf_token": "anonymous-token"})
        self.assertEqual(
            self.create(anonymous, "not allowed", token="anonymous-token").status_code,
            401
        )
        created = self.create(self.student, "delete me")
        post_id = response_json(created)["post"]["id"]
        non_admin_delete = self.await_result(
            main.AdminDeleteCommunityPost(self.student, post_id, "token", 1)
        )
        self.assertEqual(non_admin_delete.status_code, 303)
        result = self.await_result(main.AdminDeleteCommunityPost(self.admin, post_id, "admin-token", 1))
        self.assertEqual(result.status_code, 303)
        self.assertEqual(self.create(self.other, "late reply", post_id, "token-b").status_code, 404)
        self.assertEqual(self.await_result(main.ToggleCommunityLike(self.other, post_id, "token-b")).status_code, 404)

    def test_cursor_pagination_has_no_duplicates(self):
        with patch.object(main, "COMMUNITY_PAGE_SIZE", 2):
            for number in range(5):
                self.create(self.student, f"post {number}")
            first = response_json(self.await_result(main.CommunityPosts(self.student)))
            second = response_json(self.await_result(main.CommunityPosts(self.student, first["next_cursor"])))
            third = response_json(self.await_result(main.CommunityPosts(self.student, second["next_cursor"])))
        ids = [post["id"] for page in (first, second, third) for post in page["posts"]]
        self.assertEqual(len(ids), 5)
        self.assertEqual(len(ids), len(set(ids)))
        self.assertFalse(third["has_more"])
        self.assertIsNone(third["next_cursor"])


if __name__ == "__main__":
    unittest.main()
