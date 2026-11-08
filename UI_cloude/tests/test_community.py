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
CREATE TABLE student (
    id TEXT NOT NULL, pwd TEXT NOT NULL, school TEXT NOT NULL,
    profile_image TEXT
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
        nested_id = response_json(nested)["post"]["id"]

        for post_id in (root_id, reply_id, nested_id):
            liked = self.await_result(main.ToggleCommunityLike(self.student, post_id, "token"))
            self.assertEqual(response_json(liked), {"liked": True, "like_count": 1})

        forbidden = self.await_result(main.DeleteCommunityPost(self.other, root_id, "token-b"))
        self.assertEqual(forbidden.status_code, 403)
        bad_csrf = self.await_result(main.DeleteCommunityPost(self.student, root_id, "wrong"))
        self.assertEqual(bad_csrf.status_code, 403)
        anonymous = FakeRequest({"community_csrf_token": "anonymous-token"})
        unauthenticated = self.await_result(
            main.DeleteCommunityPost(anonymous, root_id, "anonymous-token")
        )
        self.assertEqual(unauthenticated.status_code, 401)
        deleted = self.await_result(main.DeleteCommunityPost(self.student, root_id, "token"))
        self.assertEqual(deleted.status_code, 200)
        posts = response_json(self.await_result(main.CommunityPosts(self.student)))
        self.assertEqual(posts["posts"], [])
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM community_post").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM community_like").fetchone()[0], 0)
        self.assertEqual(self.create(self.other, "late reply", root_id, "token-b").status_code, 404)
        self.assertEqual(self.await_result(main.ToggleCommunityLike(self.other, root_id, "token-b")).status_code, 404)

    def test_delete_reply_keeps_parent_and_sibling(self):
        root_id = response_json(self.create(self.student, "親"))["post"]["id"]
        reply_id = response_json(self.create(self.student, "削除対象", root_id))["post"]["id"]
        nested_id = response_json(self.create(self.other, "配下", reply_id, "token-b"))["post"]["id"]
        sibling_id = response_json(self.create(self.other, "兄弟", root_id, "token-b"))["post"]["id"]

        deleted = self.await_result(main.DeleteCommunityPost(self.student, reply_id, "token"))
        self.assertEqual(deleted.status_code, 200)
        with closing(sqlite3.connect(self.db_path)) as db:
            remaining = {row[0] for row in db.execute("SELECT id FROM community_post")}
        self.assertEqual(remaining, {root_id, sibling_id})
        self.assertNotIn(reply_id, remaining)
        self.assertNotIn(nested_id, remaining)

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
        with closing(sqlite3.connect(self.db_path)) as db:
            self.assertIsNone(db.execute("SELECT id FROM community_post WHERE id = ?", (post_id,)).fetchone())
        self.assertEqual(self.create(self.other, "late reply", post_id, "token-b").status_code, 404)
        self.assertEqual(self.await_result(main.ToggleCommunityLike(self.other, post_id, "token-b")).status_code, 404)

    def test_cleanup_removes_legacy_deleted_subtree_and_likes(self):
        with closing(sqlite3.connect(self.db_path)) as db:
            db.execute(
                "INSERT INTO community_post (id, user_id, content, created_at, deleted_at) VALUES (1, 'a', 'old', '2026-01-01T00:00:00+09:00', '2026-01-02')"
            )
            db.execute(
                "INSERT INTO community_post (id, user_id, parent_id, content, created_at) VALUES (2, 'b', 1, 'reply', '2026-01-01T00:01:00+09:00')"
            )
            db.execute(
                "INSERT INTO community_like (post_id, user_id, created_at) VALUES (2, 'a', '2026-01-01T00:02:00+09:00')"
            )
            db.commit()
            db.execute("BEGIN IMMEDIATE")
            removed = main.cleanup_deleted_community_posts(db)
            db.commit()
            self.assertEqual(removed, 2)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM community_post").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM community_like").fetchone()[0], 0)

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
