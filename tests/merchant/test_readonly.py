"""These exercise a real SQLite connection, not mocked tool responses."""

import sqlite3
import tempfile
import unittest
from pathlib import Path

from analytics_agent.merchant.readonly import SnapshotReader


class ReadonlyTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / "snapshot.db"
        with sqlite3.connect(self.path) as conn:
            conn.execute("CREATE TABLE orders(id INTEGER PRIMARY KEY, amount INTEGER)")
            conn.executemany("INSERT INTO orders VALUES (?, ?)", [(1, 100), (2, 200), (3, 300)])
        self.reader = SnapshotReader(self.path, max_rows=2)

    def test_aggregate(self):
        self.assertEqual(
            self.reader.query("SELECT SUM(amount) AS total FROM orders")["rows"], [{"total": 600}]
        )

    def test_cte_result_limit(self):
        result = self.reader.query("WITH x AS (SELECT * FROM orders) SELECT * FROM x")
        self.assertTrue(result["truncated"])
        self.assertEqual(len(result["rows"]), 2)

    def test_exact_limit_not_truncated(self):
        self.assertFalse(self.reader.query("SELECT * FROM orders LIMIT 2")["truncated"])

    def test_mutations_rejected_and_snapshot_intact(self):
        statements = [
            "DELETE FROM orders",
            "UPDATE orders SET amount=0",
            "DROP TABLE orders",
            "CREATE TABLE other(x)",
            "PRAGMA query_only=OFF",
            "ATTACH DATABASE ':memory:' AS other",
            "SELECT load_extension('anything')",
            "SELECT 1; DELETE FROM orders",
        ]
        for statement in statements:
            with self.subTest(statement=statement):
                self.assertIn("error", self.reader.query(statement))
        self.assertEqual(
            self.reader.query("SELECT SUM(amount) AS total FROM orders")["rows"], [{"total": 600}]
        )

    def test_recursive_query_timeout(self):
        reader = SnapshotReader(self.path, timeout_seconds=0.01)
        result = reader.query(
            "WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x) "
            "SELECT SUM(n) AS total FROM x"
        )
        self.assertEqual(result["error"], "query_timeout")

    def test_duplicate_columns_cannot_silently_overwrite_evidence(self):
        self.assertEqual(
            self.reader.query("SELECT 1 AS x, 2 AS x")["error"], "duplicate_columns_use_aliases"
        )


if __name__ == "__main__":
    unittest.main()
