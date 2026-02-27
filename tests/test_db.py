import os
import sqlite3
import unittest

from mfaren import db


class TestDB(unittest.TestCase):
    def test_init_db_creates_tables_and_migrates(self):
        test_db = os.path.join("data", "test_media_faren.db")
        if os.path.exists(test_db):
            os.remove(test_db)
        db.DB_PATH = test_db
        db.init_db()
        conn = sqlite3.connect(test_db)
        cur = conn.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        tables = {row[0] for row in cur.fetchall()}
        self.assertIn("settings", tables)
        self.assertIn("jobs", tables)
        cur.execute("PRAGMA user_version")
        version = cur.fetchone()[0]
        self.assertGreaterEqual(version, 2)
        cur.execute("PRAGMA table_info(jobs)")
        columns = {row[1] for row in cur.fetchall()}
        self.assertIn("parent_job_id", columns)
        self.assertIn("pid", columns)
        self.assertIn("last_event_at", columns)
        conn.close()


if __name__ == "__main__":
    unittest.main()
