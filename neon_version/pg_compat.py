"""Small SQLite-shaped adapter for the PostgreSQL edition of this app.

SQL remains explicit in the application. This module only adapts the handful of
SQLite syntax and cursor conveniences used by the existing routes.
"""
import os
import re
import threading

import psycopg


Error = psycopg.Error
IntegrityError = psycopg.IntegrityError


class Row:
    def __init__(self, values, names):
        self._values = tuple(values)
        self._names = tuple(names)

    def __getitem__(self, key):
        if isinstance(key, str):
            return self._values[self._names.index(key)]
        return self._values[key]

    def __iter__(self):
        return iter(self._values)

    def __len__(self):
        return len(self._values)

    def keys(self):
        return list(self._names)


_ID_TABLES = {
    "study", "reservation", "reservation_available_slot", "equipment_reservation",
    "equipment_room_reservation", "community_post", "community_like",
    "notification_batch", "notification", "admin_notification", "study_template",
    "study_template_field", "study_field_image", "mentor_available_slot",
    "mentor_reservation",
}


def _placeholders(sql):
    out = []
    quote = None
    i = 0
    while i < len(sql):
        char = sql[i]
        if quote:
            out.append(char)
            if char == quote:
                if i + 1 < len(sql) and sql[i + 1] == quote:
                    out.append(sql[i + 1])
                    i += 1
                else:
                    quote = None
        elif char in ("'", '"'):
            quote = char
            out.append(char)
        elif char == "?":
            out.append("%s")
        else:
            out.append(char)
        i += 1
    return "".join(out)


def _translate(sql):
    sql = sql.strip().removesuffix(";").rstrip()
    if re.fullmatch(r"PRAGMA\s+foreign_keys\s*=\s*ON", sql, re.I):
        return "SELECT 1"
    table_info = re.fullmatch(r"PRAGMA\s+table_info\((\w+)\)", sql, re.I)
    if table_info:
        table = table_info.group(1)
        return ("SELECT ordinal_position - 1, column_name FROM information_schema.columns "
                f"WHERE table_schema = current_schema() AND table_name = '{table}' "
                "ORDER BY ordinal_position")
    sql = re.sub(r"INTEGER\s+PRIMARY\s+KEY\s+AUTOINCREMENT", "SERIAL PRIMARY KEY", sql, flags=re.I)
    sql = re.sub(r"\bid\s+INTEGER\s+PRIMARY\s+KEY\b", "id SERIAL PRIMARY KEY", sql, flags=re.I)
    sql = re.sub(r"\bINSERT\s+OR\s+IGNORE\s+INTO\b", "INSERT INTO", sql, flags=re.I)
    return _placeholders(sql)


class Cursor:
    def __init__(self, connection, dedicated=False):
        self.connection = connection
        self.dedicated = dedicated
        self._local = threading.local()

    def _cursor(self):
        if self.dedicated:
            cursor = getattr(self._local, "cursor", None)
            if cursor is None or cursor.closed:
                cursor = self.connection._raw().cursor()
                self._local.cursor = cursor
            return cursor
        return self.connection._raw().cursor()

    def execute(self, sql, params=()):
        original = sql.strip()
        if original.upper() == "BEGIN IMMEDIATE":
            sql = "SELECT pg_advisory_xact_lock(72493612)"
        elif re.search(r"\bFROM\s+sqlite_master\b", original, re.I):
            sql = re.sub(r"\bFROM\s+sqlite_master\b", "FROM pg_catalog.pg_tables", original, flags=re.I)
            sql = sql.replace("type = 'table' AND name", "schemaname = current_schema() AND tablename")
            sql = _placeholders(sql)
        else:
            sql = _translate(original)
        if re.search(r"INSERT\s+OR\s+IGNORE\s+INTO", original, re.I):
            sql += " ON CONFLICT DO NOTHING"
        insert = re.match(r"INSERT\s+INTO\s+(\w+)", sql, re.I)
        wants_id = insert and insert.group(1).lower() in _ID_TABLES and not re.search(r"\bRETURNING\b", sql, re.I)
        if wants_id:
            sql += " RETURNING id"
        raw = self._cursor()
        raw.execute(sql, tuple(params))
        self._local.current = raw
        self._local.pending = raw.fetchone() if wants_id and raw.rowcount else None
        self._local.lastrowid = self._local.pending[0] if self._local.pending else None
        return self

    def executemany(self, sql, seq):
        for params in seq:
            self.execute(sql, params)
        return self

    def executescript(self, sql):
        for statement in sql.split(";"):
            if statement.strip():
                self.execute(statement)
        return self

    def fetchone(self):
        row = self._local.pending
        self._local.pending = None
        if row is None:
            row = self._local.current.fetchone()
        return self._convert(row)

    def fetchall(self):
        pending = self._local.pending
        self._local.pending = None
        rows = self._local.current.fetchall()
        if pending is not None:
            rows.insert(0, pending)
        return [self._convert(row) for row in rows]

    def _convert(self, row):
        if row is None or self.connection.row_factory is not Row:
            return row
        return Row(row, [col.name for col in self._local.current.description])

    def __iter__(self):
        return iter(self.fetchall())

    @property
    def rowcount(self):
        return self._local.current.rowcount

    @property
    def lastrowid(self):
        return getattr(self._local, "lastrowid", None)


class Connection:
    def __init__(self, global_connection=False):
        if not os.environ.get("DATABASE_URL"):
            raise RuntimeError("DATABASE_URL is required for neon_version")
        self.global_connection = global_connection
        self.row_factory = None
        self._local = threading.local()
        self._connection = None if global_connection else self._new_connection()

    @staticmethod
    def _new_connection(autocommit=False):
        url = os.environ.get("DATABASE_URL")
        if not url:
            raise RuntimeError("DATABASE_URL is required for neon_version")
        return psycopg.connect(url, autocommit=autocommit, sslmode="require")

    def _raw(self):
        if not self.global_connection:
            return self._connection
        raw = getattr(self._local, "connection", None)
        if raw is None or raw.closed:
            raw = self._new_connection(autocommit=True)
            self._local.connection = raw
        return raw

    def cursor(self):
        return Cursor(self, dedicated=self.global_connection)

    def execute(self, sql, params=()):
        return self.cursor().execute(sql, params)

    def executemany(self, sql, seq):
        return self.cursor().executemany(sql, seq)

    def executescript(self, sql):
        return self.cursor().executescript(sql)

    def commit(self):
        self._raw().commit()

    def rollback(self):
        self._raw().rollback()

    def close(self):
        self._raw().close()

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        self.rollback() if exc_type else self.commit()


def connect(_path=None, timeout=None, check_same_thread=None):
    return Connection(global_connection=check_same_thread is False)
