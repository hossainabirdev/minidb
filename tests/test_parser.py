"""Tests for minidb.parser -- tokenizer + recursive-descent parser."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from minidb.parser import (
    Aggregate,
    BinaryOp,
    ColumnRef,
    CreateTableStmt,
    DeleteStmt,
    InsertStmt,
    Literal,
    MiniDBError,
    SelectStmt,
    UpdateStmt,
    parse,
)


class TestParser(unittest.TestCase):
    def test_create_table(self):
        stmt = parse(
            "CREATE TABLE users (id INTEGER PRIMARY KEY, name TEXT NOT NULL, age INTEGER)"
        )
        self.assertIsInstance(stmt, CreateTableStmt)
        self.assertEqual(stmt.table, "users")
        self.assertEqual(len(stmt.columns), 3)
        self.assertTrue(stmt.columns[0]["primary_key"])
        self.assertTrue(stmt.columns[1]["not_null"])
        self.assertEqual(stmt.columns[2]["type"], "INTEGER")

    def test_create_table_table_level_pk(self):
        stmt = parse("CREATE TABLE t (id INTEGER, name TEXT, PRIMARY KEY (id))")
        self.assertTrue(stmt.columns[0]["primary_key"])

    def test_insert_single_and_multi_row(self):
        stmt = parse("INSERT INTO users (id, name) VALUES (1, 'Abir'), (2, 'Sara')")
        self.assertIsInstance(stmt, InsertStmt)
        self.assertEqual(len(stmt.rows), 2)
        self.assertIsInstance(stmt.rows[0][1], Literal)

    def test_select_star_where_and_or(self):
        stmt = parse(
            "SELECT * FROM users WHERE age > 20 AND (name = 'Abir' OR name = 'Sara')"
        )
        self.assertIsInstance(stmt, SelectStmt)
        self.assertEqual(stmt.table, "users")
        self.assertIsInstance(stmt.where, BinaryOp)
        self.assertEqual(stmt.where.op, "AND")

    def test_select_order_limit_offset(self):
        stmt = parse("SELECT id, name FROM users ORDER BY age DESC, name ASC LIMIT 5 OFFSET 2")
        self.assertEqual(stmt.order_by, [("age", "DESC"), ("name", "ASC")])
        self.assertEqual(stmt.limit, 5)
        self.assertEqual(stmt.offset, 2)

    def test_select_aggregate(self):
        stmt = parse("SELECT COUNT(*), SUM(age), AVG(age) FROM users")
        self.assertEqual(len(stmt.columns), 3)
        self.assertIsInstance(stmt.columns[0].expr, Aggregate)
        self.assertTrue(stmt.columns[0].expr.is_star)

    def test_update_delete(self):
        upd = parse("UPDATE users SET age = 21 WHERE id = 1")
        self.assertIsInstance(upd, UpdateStmt)
        self.assertEqual(len(upd.assignments), 1)
        dele = parse("DELETE FROM users WHERE id = 2")
        self.assertIsInstance(dele, DeleteStmt)

    def test_not_and_parentheses(self):
        stmt = parse("SELECT * FROM t WHERE NOT (a = 1 OR b = 2)")
        # Top level is NOT, represented as UnaryOp wrapping an OR.
        from minidb.parser import UnaryOp

        self.assertIsInstance(stmt.where, UnaryOp)
        self.assertEqual(stmt.where.op, "NOT")
        self.assertIsInstance(stmt.where.operand, BinaryOp)
        self.assertEqual(stmt.where.operand.op, "OR")

    def test_string_escape(self):
        stmt = parse("INSERT INTO t (name) VALUES ('O''Brien')")
        self.assertEqual(stmt.rows[0][0].value, "O'Brien")

    def test_negative_number(self):
        stmt = parse("INSERT INTO t (x) VALUES (-5)")
        # -5 is UnaryOp('-', Literal(5))
        from minidb.parser import UnaryOp

        self.assertIsInstance(stmt.rows[0][0], UnaryOp)

    def test_unsupported_join_raises(self):
        with self.assertRaises(MiniDBError):
            parse("SELECT * FROM a JOIN b ON a.id = b.id")

    def test_unsupported_begin_raises(self):
        with self.assertRaises(MiniDBError):
            parse("BEGIN")

    def test_empty_raises(self):
        with self.assertRaises(MiniDBError):
            parse("   ")

    def test_bad_syntax_raises(self):
        with self.assertRaises(MiniDBError):
            parse("SELECT FROM users")
        with self.assertRaises(MiniDBError):
            parse("CREATE TABLE t (id WRONGTYPE)")


if __name__ == "__main__":
    unittest.main()
