import io
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path
from unittest.mock import patch

from PIL import Image
from starlette.datastructures import FormData, Headers, UploadFile
from starlette.requests import Request

import main


def request(session, method="GET"):
    scope = {"type": "http", "method": method, "scheme": "http", "path": "/admin/equipment-catalog",
             "server": ("test", 80), "headers": [], "query_string": b"", "session": session}
    async def receive():
        return {"type": "http.disconnect"}
    return Request(scope, receive=receive)


def run_immediate(coroutine):
    try:
        coroutine.send(None)
    except StopIteration as result:
        return result.value
    raise AssertionError("Test coroutine unexpectedly yielded")


class EquipmentCatalogTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=Path(__file__).resolve().parents[1])
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.path = root / "catalog.db"
        self.uploads = root / "equipment"
        self.uploads.mkdir()
        with closing(sqlite3.connect(self.path)) as db:
            db.executescript("""CREATE TABLE schema_migration(name TEXT PRIMARY KEY, applied_at TEXT NOT NULL);
                CREATE TABLE equipment_catalog(id TEXT PRIMARY KEY,name TEXT NOT NULL,image TEXT NOT NULL,content TEXT NOT NULL,count INTEGER NOT NULL,usage_type TEXT NOT NULL);
                CREATE TABLE equipment_reservation(id INTEGER PRIMARY KEY,userid TEXT,equipment TEXT,equipment_id TEXT);
                CREATE TABLE equipment_room_reservation(id INTEGER PRIMARY KEY,userid TEXT,equipment TEXT,equipment_id TEXT);""")
            db.commit()
        self.db_patch = patch.object(main, "DATABASE_PATH", self.path)
        self.upload_patch = patch.object(main, "EQUIPMENT_UPLOADS_DIR", self.uploads)
        self.db_patch.start()
        self.upload_patch.start()
        self.addCleanup(self.db_patch.stop)
        self.addCleanup(self.upload_patch.stop)

    def test_csv_migration_runs_once_and_preserves_catalog(self):
        csv_path = Path(self.temp.name) / "equipment.csv"
        csv_path.write_text("id,name,image,content,count,usage_type\nEQ001,Microscope,/static/m.svg,Observe,3,takeout\n", encoding="utf-8")
        with closing(sqlite3.connect(self.path)) as db:
            main.initialize_equipment_catalog(db, csv_path)
            db.execute("UPDATE equipment_catalog SET count=4 WHERE id='EQ001'")
            csv_path.write_text("id,name,image,content,count,usage_type\nEQ002,Changed,/static/x.svg,,1,in_room\n", encoding="utf-8")
            main.initialize_equipment_catalog(db, csv_path)
            self.assertEqual(db.execute("SELECT id,name,count FROM equipment_catalog").fetchall(), [("EQ001", "Microscope", 4)])
            self.assertEqual(db.execute("SELECT COUNT(*) FROM schema_migration WHERE name='equipment_catalog_csv_v1'").fetchone()[0], 1)

    def test_catalog_is_loaded_from_database(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("INSERT INTO equipment_catalog VALUES('EQ001','Microscope','/static/m.svg','Observe',3,'takeout')")
            db.commit()
        self.assertEqual(main.load_equipment_catalog(), [{"id": "EQ001", "name": "Microscope", "image": "/static/m.svg",
                         "content": "Observe", "count": 3, "usage_type": "takeout"}])

    def test_create_uploads_valid_image_and_rejects_bad_image_without_record(self):
        session = {"admin_login": True, "equipment_catalog_csrf_token": "csrf-token"}
        source = io.BytesIO()
        Image.new("RGB", (24, 16), "#44aa66").save(source, format="PNG")
        upload_file = tempfile.SpooledTemporaryFile()
        upload_file.write(source.getvalue())
        upload_file.seek(0)
        upload = UploadFile(file=upload_file, filename="tool.png",
                            headers=Headers({"content-type": "image/png"}))
        req = request(session, "POST")
        req._form = FormData({"csrf_token": "csrf-token", "name": "Microscope", "content": "Observation",
                              "count": "2", "usage_type": "takeout", "image": upload})
        response = run_immediate(main.AdminEquipmentCatalogCreate(req))
        self.assertEqual(response.status_code, 303)
        self.assertEqual(len(list(self.uploads.glob("*.webp"))), 1)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT name,count,usage_type,image FROM equipment_catalog").fetchone()[0:3],
                             ("Microscope", 2, "takeout"))
            image_url = db.execute("SELECT image FROM equipment_catalog").fetchone()[0]
            self.assertTrue(image_url.startswith("/uploads/equipment/"))

        bad_file = tempfile.SpooledTemporaryFile()
        bad_file.write(b"not an image")
        bad_file.seek(0)
        bad_upload = UploadFile(file=bad_file, filename="bad.png",
                                headers=Headers({"content-type": "image/png"}))
        bad_req = request(session, "POST")
        bad_req._form = FormData({"csrf_token": session["equipment_catalog_csrf_token"], "name": "Bad", "content": "",
                                  "count": "1", "usage_type": "in_room", "image": bad_upload})
        run_immediate(main.AdminEquipmentCatalogCreate(bad_req))
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM equipment_catalog").fetchone()[0], 1)

    def test_delete_rejects_reservation_history_and_csrf(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("INSERT INTO equipment_catalog VALUES('EQ001','Microscope','/static/m.svg','',1,'takeout')")
            db.execute("INSERT INTO equipment_reservation(userid,equipment,equipment_id) VALUES('s','Microscope','EQ001')")
            db.commit()
        session = {"admin_login": True, "equipment_catalog_csrf_token": "csrf-token"}
        with self.assertRaises(Exception) as error:
            run_immediate(main.AdminEquipmentCatalogDelete(request(session, "POST"), "EQ001", "wrong"))
        self.assertEqual(getattr(error.exception, "status_code", None), 403)
        response = run_immediate(main.AdminEquipmentCatalogDelete(request(session, "POST"), "EQ001", "csrf-token"))
        self.assertEqual(response.status_code, 303)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM equipment_catalog").fetchone()[0], 1)

    def test_update_changes_fields_and_keeps_or_replaces_image(self):
        old_image = self.uploads / "old.webp"
        old_image.write_bytes(b"old")
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("INSERT INTO equipment_catalog VALUES('EQ001','Microscope','/uploads/equipment/old.webp','Old',1,'takeout')")
            db.commit()
        session = {"admin_login": True, "equipment_catalog_csrf_token": "csrf-token"}
        req = request(session, "POST")
        req._form = FormData({"csrf_token": "csrf-token", "name": "Updated scope", "content": "New description",
                              "count": "4", "usage_type": "in_room"})
        response = run_immediate(main.AdminEquipmentCatalogUpdate(req, "EQ001"))
        self.assertEqual(response.status_code, 303)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT id,name,image,content,count,usage_type FROM equipment_catalog").fetchone(),
                             ("EQ001", "Updated scope", "/uploads/equipment/old.webp", "New description", 4, "in_room"))
        self.assertTrue(old_image.exists())

        source = io.BytesIO()
        Image.new("RGB", (8, 8), "#8844aa").save(source, format="PNG")
        upload_file = tempfile.SpooledTemporaryFile()
        upload_file.write(source.getvalue())
        upload_file.seek(0)
        upload = UploadFile(file=upload_file, filename="replacement.png", headers=Headers({"content-type": "image/png"}))
        req = request(session, "POST")
        req._form = FormData({"csrf_token": session["equipment_catalog_csrf_token"], "name": "Updated scope",
                              "content": "New description", "count": "4", "usage_type": "in_room", "image": upload})
        response = run_immediate(main.AdminEquipmentCatalogUpdate(req, "EQ001"))
        self.assertEqual(response.status_code, 303)
        self.assertFalse(old_image.exists())
        with closing(sqlite3.connect(self.path)) as db:
            new_image = db.execute("SELECT image FROM equipment_catalog WHERE id='EQ001'").fetchone()[0]
        self.assertTrue(new_image.startswith("/uploads/equipment/"))
        self.assertEqual(len(list(self.uploads.glob("*.webp"))), 1)

    def test_delete_removes_unreserved_item(self):
        with closing(sqlite3.connect(self.path)) as db:
            db.execute("INSERT INTO equipment_catalog VALUES('EQ001','Microscope','/static/m.svg','',1,'takeout')")
            db.commit()
        session = {"admin_login": True, "equipment_catalog_csrf_token": "csrf-token"}
        response = run_immediate(main.AdminEquipmentCatalogDelete(request(session, "POST"), "EQ001", "csrf-token"))
        self.assertEqual(response.status_code, 303)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM equipment_catalog").fetchone()[0], 0)

    def test_admin_authentication_and_image_write_failure(self):
        response = run_immediate(main.AdminEquipmentCatalogDelete(request({}, "POST"), "EQ001", ""))
        self.assertEqual(response.status_code, 303)
        self.assertEqual(response.headers["location"], "/admin/login")

        session = {"admin_login": True, "equipment_catalog_csrf_token": "csrf-token"}
        source = io.BytesIO()
        Image.new("RGB", (8, 8), "#4488aa").save(source, format="PNG")
        upload_file = tempfile.SpooledTemporaryFile()
        upload_file.write(source.getvalue())
        upload_file.seek(0)
        upload = UploadFile(file=upload_file, filename="tool.png", headers=Headers({"content-type": "image/png"}))
        req = request(session, "POST")
        req._form = FormData({"csrf_token": "csrf-token", "name": "Tool", "content": "", "count": "1",
                              "usage_type": "takeout", "image": upload})
        with patch.object(Path, "write_bytes", side_effect=OSError("disk full")):
            response = run_immediate(main.AdminEquipmentCatalogCreate(req))
        self.assertEqual(response.status_code, 303)
        with closing(sqlite3.connect(self.path)) as db:
            self.assertEqual(db.execute("SELECT COUNT(*) FROM equipment_catalog").fetchone()[0], 0)


if __name__ == "__main__":
    unittest.main()
