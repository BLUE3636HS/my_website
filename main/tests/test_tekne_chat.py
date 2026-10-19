import datetime
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from fastapi import HTTPException

from tekne_chat import (
    GroupConnectionManager,
    TekneChatMigrationError,
    TekneChatService,
    _clean_name,
    _connect,
    _storage_file,
    _same_origin,
    create_chat_session,
    initialize_tekne_chat,
    link_student_identity,
    rename_student_identity,
    revoke_student_identity,
)


class MemoryUpload:
    def __init__(self, filename, content=b"sample", content_type="application/octet-stream"):
        self.filename = filename
        self.file = io.BytesIO(content)
        self.content_type = content_type


class TekneChatTests(unittest.TestCase):
    def setUp(self):
        self.temp_dir = tempfile.TemporaryDirectory()
        root = Path(self.temp_dir.name)
        self.database_path = root / "chat-test.db"
        self.storage_path = root / "private-chat-storage"
        with sqlite3.connect(self.database_path) as db:
            db.executescript("""
                CREATE TABLE student(id TEXT NOT NULL, pwd TEXT NOT NULL, school TEXT NOT NULL);
                CREATE TABLE registered_school(name TEXT NOT NULL UNIQUE);
                INSERT INTO registered_school(name) VALUES ('School A'), ('School B');
                INSERT INTO student(id,pwd,school) VALUES
                    ('student_0001','hash','School A'),
                    ('student_0002','hash','School A'),
                    ('student_0003','hash','School B');
            """)
            initialize_tekne_chat(db)
        self.service = TekneChatService(self.database_path, self.storage_path)
        self.sessions = {login_id: self._session(login_id) for login_id in ("student_0001", "student_0002", "student_0003")}

    def tearDown(self):
        self.temp_dir.cleanup()

    def _session(self, login_id):
        token = create_chat_session(self.database_path, login_id)
        return {"user_login": True, "user_id": login_id,
                "user_time": datetime.datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "tekne_chat_session": token}

    def _create_group(self):
        result = self.service.create_group(self.sessions["student_0001"], "勉強会")
        return result["id"]

    def test_migration_adds_internal_ids_and_preserves_student_rows(self):
        with _connect(self.database_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM student").fetchone()[0], 3)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tekne_student_identity").fetchone()[0], 3)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tekne_school_identity").fetchone()[0], 2)
        with sqlite3.connect(self.database_path) as db:
            initialize_tekne_chat(db)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tekne_student_identity").fetchone()[0], 3)

    def test_new_student_registration_link_uses_same_school_identity(self):
        with _connect(self.database_path) as db:
            school_id = db.execute("SELECT school_id FROM tekne_student_identity WHERE login_id='student_0001'").fetchone()[0]
            db.execute("INSERT INTO student VALUES ('student_0004','hash','School A')")
            identity_id = link_student_identity(db, "student_0004", "School A")
            linked = db.execute("SELECT school_id FROM tekne_student_identity WHERE id=?", (identity_id,)).fetchone()[0]
        self.assertEqual(school_id, linked)

    def test_ambiguous_duplicate_id_stops_without_partial_chat_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "ambiguous.db"
            with sqlite3.connect(path) as db:
                db.executescript("""
                    CREATE TABLE student(id TEXT NOT NULL, pwd TEXT NOT NULL, school TEXT NOT NULL);
                    CREATE TABLE registered_school(name TEXT NOT NULL UNIQUE);
                    INSERT INTO registered_school VALUES ('School A');
                    INSERT INTO student VALUES ('student_0001','x','School A'), ('student_0001','y','School A');
                """)
                with self.assertRaises(TekneChatMigrationError):
                    initialize_tekne_chat(db)
                self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='tekne_chat_group'").fetchone())

    def test_unmapped_school_stops_before_chat_schema(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = Path(tmp) / "unknown-school.db"
            with sqlite3.connect(path) as db:
                db.executescript("""
                    CREATE TABLE student(id TEXT NOT NULL, pwd TEXT NOT NULL, school TEXT NOT NULL);
                    CREATE TABLE registered_school(name TEXT NOT NULL UNIQUE);
                    INSERT INTO registered_school VALUES ('School A');
                    INSERT INTO student VALUES ('student_0001','x','Ambiguous School Name');
                """)
                with self.assertRaises(TekneChatMigrationError):
                    initialize_tekne_chat(db)
                self.assertIsNone(db.execute("SELECT 1 FROM sqlite_master WHERE name='tekne_chat_group'").fetchone())

    def test_group_invitation_is_school_scoped_and_rejoin_restores_history(self):
        group_id = self._create_group()
        with self.assertRaises(HTTPException):
            self.service.invite(self.sessions["student_0003"], group_id, "student_0003")
        self.service.invite(self.sessions["student_0001"], group_id, "student_0002")
        message = self.service.create_message(self.sessions["student_0002"], group_id, "過去ログ")
        self.service.leave(self.sessions["student_0002"], group_id)
        with self.assertRaises(HTTPException):
            self.service.message_history(self.sessions["student_0002"], group_id)
        self.service.invite(self.sessions["student_0001"], group_id, "student_0002")
        history = self.service.message_history(self.sessions["student_0002"], group_id)
        self.assertEqual(history["messages"][0]["id"], message["id"])

    def test_group_name_and_message_edit_delete_permissions(self):
        group_id = self._create_group()
        self.service.invite(self.sessions["student_0001"], group_id, "student_0002")
        self.service.rename_group(self.sessions["student_0002"], group_id, "全員で変更")
        message = self.service.create_message(self.sessions["student_0001"], group_id, "最初")
        edited = self.service.edit_message(self.sessions["student_0001"], message["id"], "変更後")
        self.assertEqual(edited["body"], "変更後")
        with self.assertRaises(HTTPException):
            self.service.edit_message(self.sessions["student_0002"], message["id"], "他人の投稿")
        with self.assertRaises(HTTPException):
            self.service.delete_message(self.sessions["student_0002"], message["id"])
        _, deleted = self.service.delete_message(self.sessions["student_0001"], message["id"])
        self.assertTrue(deleted["deleted"])
        self.assertEqual(deleted["body"], "メッセージが削除されました")

    def test_repeated_message_submission_with_same_client_id_is_idempotent(self):
        group_id = self._create_group()
        client_id = "retry-event-0123456789"
        first = self.service.create_message(self.sessions["student_0001"], group_id, "一度だけ保存", client_id)
        second = self.service.create_message(self.sessions["student_0001"], group_id, "一度だけ保存", client_id)
        self.assertEqual(first["id"], second["id"])
        self.assertEqual(len(self.service.message_history(self.sessions["student_0001"], group_id)["messages"]), 1)

    def test_message_length_and_file_name_path_sanitization(self):
        group_id = self._create_group()
        with self.assertRaises(HTTPException):
            self.service.create_message(self.sessions["student_0001"], group_id, "x" * 5001)
        uploaded = self.service.upload_file(self.sessions["student_0001"], group_id, None,
                                            MemoryUpload("../../outside.txt", b"safe"))
        self.assertEqual(uploaded["file_name"], "outside.txt")
        self.assertIsNone(_clean_name("../.."))
        with self.assertRaises(ValueError):
            _storage_file(self.storage_path, "../outside")

    def test_folder_cycle_is_detected_and_other_school_cannot_open_file(self):
        group_id = self._create_group()
        folder = self.service.create_folder(self.sessions["student_0001"], group_id, "循環検出")
        with _connect(self.database_path) as db:
            db.execute("UPDATE tekne_chat_folder SET parent_id=? WHERE id=?", (folder["id"], folder["id"]))
        with self.assertRaises(HTTPException) as raised:
            self.service.list_files(self.sessions["student_0001"], group_id, folder["id"])
        self.assertEqual(raised.exception.status_code, 409)
        with _connect(self.database_path) as db:
            db.execute("UPDATE tekne_chat_folder SET parent_id=NULL WHERE id=?", (folder["id"],))
        file_message = self.service.upload_file(self.sessions["student_0001"], group_id, None, MemoryUpload("private.dat"))
        with self.assertRaises(HTTPException):
            self.service.get_file(self.sessions["student_0003"], file_message["file_id"])

    def test_login_id_change_keeps_immutable_identity_and_session(self):
        with _connect(self.database_path) as db:
            before = db.execute("SELECT id FROM tekne_student_identity WHERE login_id='student_0001'").fetchone()[0]
            rename_student_identity(db, "student_0001", "student_1001")
            db.execute("UPDATE student SET id='student_1001' WHERE id='student_0001'")
        session = dict(self.sessions["student_0001"], user_id="student_1001")
        self.service.create_group(session, "ID変更後")
        with _connect(self.database_path) as db:
            after = db.execute("SELECT id FROM tekne_student_identity WHERE login_id='student_1001'").fetchone()[0]
        self.assertEqual(before, after)

    def test_deleted_student_is_anonymized_and_cannot_access_group(self):
        group_id = self._create_group()
        message = self.service.create_message(self.sessions["student_0001"], group_id, "残す投稿")
        with _connect(self.database_path) as db:
            revoke_student_identity(db, "student_0001")
            db.execute("DELETE FROM student WHERE id='student_0001'")
        with self.assertRaises(HTTPException):
            self.service.message_history(self.sessions["student_0001"], group_id)
        with _connect(self.database_path) as db:
            stored = db.execute("SELECT * FROM tekne_chat_message WHERE id=?", (message["id"],)).fetchone()
            from tekne_chat import _response_message
            self.assertEqual(_response_message(db, stored)["sender"], "削除済みユーザー")

    def test_file_name_collision_private_download_metadata_and_folder_delete(self):
        group_id = self._create_group()
        first_folder = self.service.create_folder(self.sessions["student_0001"], group_id, "資料")
        second_folder = self.service.create_folder(self.sessions["student_0001"], group_id, "年次", first_folder["id"])
        first = self.service.upload_file(self.sessions["student_0001"], group_id, second_folder["id"], MemoryUpload("report.pdf"))
        second = self.service.upload_file(self.sessions["student_0001"], group_id, second_folder["id"], MemoryUpload("report.pdf"))
        self.assertEqual(first["file_name"], "report.pdf")
        self.assertEqual(second["file_name"], "report (1).pdf")
        with _connect(self.database_path) as db:
            self.assertTrue(_storage_file(self.storage_path, self.service.file_record(db, first["file_id"])["object_key"]).is_file())
        listed = self.service.list_files(self.sessions["student_0001"], group_id, second_folder["id"])
        self.assertEqual(len(listed["files"]), 2)
        _, deleted = self.service.delete_folder(self.sessions["student_0001"], first_folder["id"])
        self.assertEqual(len(deleted), 2)
        with _connect(self.database_path) as db:
            with self.assertRaises(HTTPException):
                self.service.file_record(db, first["file_id"])

    def test_file_rename_move_and_search(self):
        group_id = self._create_group()
        folder = self.service.create_folder(self.sessions["student_0001"], group_id, "保存先")
        file_message = self.service.upload_file(self.sessions["student_0001"], group_id, None, MemoryUpload("draft.txt"))
        file_id = file_message["file_id"]
        renamed = self.service.rename_file(self.sessions["student_0001"], file_id, "final.txt")
        self.assertEqual(renamed["display_name"], "final.txt")
        moved = self.service.move_file(self.sessions["student_0001"], file_id, folder["id"])
        self.assertEqual(moved["folder_id"], folder["id"])
        results = self.service.list_files(self.sessions["student_0001"], group_id, query="final")
        self.assertEqual(results["files"][0]["id"], file_id)
        self.assertEqual(results["files"][0]["uploaded_by_login_id"], "student_0001")

    def test_admin_can_remove_member_and_delete_other_users_message(self):
        group_id = self._create_group()
        self.service.invite(self.sessions["student_0001"], group_id, "student_0002")
        message = self.service.create_message(self.sessions["student_0002"], group_id, "管理者削除対象")
        with _connect(self.database_path) as db:
            member_id = self.service._identity(db, self.sessions["student_0002"])[0]
        self.service.admin_remove_member("admin-test-id", group_id, member_id)
        with self.assertRaises(HTTPException):
            self.service.message_history(self.sessions["student_0002"], group_id)
        _, deleted = self.service.delete_message({}, message["id"], admin_id="admin-test-id")
        self.assertTrue(deleted["deleted"])
        with _connect(self.database_path) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM tekne_chat_admin_audit").fetchone()[0], 2)

    def test_expired_or_revoked_chat_session_is_rejected(self):
        group_id = self._create_group()
        with _connect(self.database_path) as db:
            db.execute("UPDATE tekne_student_session SET revoked_at='now' WHERE student_id=(SELECT id FROM tekne_student_identity WHERE login_id='student_0001')")
        with self.assertRaises(HTTPException):
            self.service.message_history(self.sessions["student_0001"], group_id)

    def test_school_change_invalidates_existing_membership(self):
        group_id = self._create_group()
        with _connect(self.database_path) as db:
            db.execute("UPDATE student SET school='School B' WHERE id='student_0001'")
        with self.assertRaises(HTTPException):
            self.service.check_socket_membership(self.sessions["student_0001"], group_id)

    def test_websocket_membership_origin_and_reconnect_history(self):
        group_id = self._create_group()
        self.service.check_socket_membership(self.sessions["student_0001"], group_id)
        class FakeUrl:
            scheme = "ws"
        class FakeSocket:
            url = FakeUrl()
            headers = {"origin": "http://localhost:8000", "host": "localhost:8000"}
        self.assertTrue(_same_origin(FakeSocket()))
        FakeSocket.headers = {"origin": "https://example.test", "host": "localhost:8000"}
        self.assertFalse(_same_origin(FakeSocket()))
        first = self.service.create_message(self.sessions["student_0001"], group_id, "before reconnect")
        second = self.service.create_message(self.sessions["student_0001"], group_id, "after reconnect")
        recovered = self.service.message_history(self.sessions["student_0001"], group_id, after_id=first["id"])
        self.assertEqual([message["id"] for message in recovered["messages"]], [second["id"]])
        with _connect(self.database_path) as db:
            db.execute("UPDATE tekne_student_session SET revoked_at='now' WHERE token_hash IS NOT NULL")
        with self.assertRaises(HTTPException):
            self.service.check_socket_membership(self.sessions["student_0001"], group_id)

    def test_websocket_group_broadcast_manager(self):
        class FakeSocket:
            def __init__(self):
                self.events = []
                self.close_code = None
            async def send_json(self, event):
                self.events.append(event)
            async def close(self, code=1000):
                self.close_code = code
        async def exercise():
            group_id = self._create_group()
            manager = GroupConnectionManager(self.service)
            first, second = FakeSocket(), FakeSocket()
            session = self.sessions["student_0001"]
            await manager.add(group_id, first, session)
            await manager.add(group_id, second, session)
            await manager.broadcast(group_id, {"type": "message.created", "id": 1})
            self.service.leave(session, group_id)
            await manager.broadcast(group_id, {"type": "message.created", "id": 2})
            self.assertEqual(first.events[0]["id"], 1)
            self.assertEqual(second.events[0]["type"], "message.created")
            self.assertEqual(first.close_code, 4403)
            self.assertEqual(second.close_code, 4403)
        import asyncio
        asyncio.run(exercise())


if __name__ == "__main__":
    unittest.main()
