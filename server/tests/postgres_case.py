import unittest
from pathlib import Path
from uuid import uuid4

from psycopg import sql

from app.database import get_connection


class PostgresIntegrationCase(unittest.TestCase):
    def setUp(self):
        self.schema = f"greentwin_tests_{uuid4().hex}"
        with get_connection() as conn:
            conn.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(self.schema)))
        self.addCleanup(self.drop_schema)

        ddl = Path(__file__).resolve().parents[1] / "db" / "tables_setting.sql"
        schema_sql = sql.Identifier(self.schema).as_string()
        source = ddl.read_text()
        expected = "SET LOCAL search_path = public;"
        self.assertEqual(source.count(expected), 1, "테스트 스키마 지정 위치를 확인하세요.")
        isolated_ddl = source.replace(expected, f"SET LOCAL search_path = {schema_sql};")
        with self.connect() as conn:
            conn.execute(isolated_ddl, prepare=False)
            self.plant_id = conn.execute(
                "INSERT INTO plant (name, species, sown_at) VALUES ('API 테스트', 'test', now()) RETURNING id"
            ).fetchone()[0]

    def connect(self):
        conn = get_connection()
        try:
            conn.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(self.schema)))
            conn.commit()
        except Exception:
            conn.close()
            raise
        return conn

    def drop_schema(self):
        with get_connection() as conn:
            conn.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(self.schema)))
