"""Parser: turn SQL text into a small tree (AST).

This is a hand-written parser, in two steps, exactly like a real
database does it:

1. Tokenizer -- splits "SELECT * FROM users" into tokens:
   SELECT, *, FROM, users.  It knows about strings, numbers and
   symbols, but nothing about what the SQL *means*.
2. Recursive-descent parser -- reads the tokens in order and
   builds a tree.  Each grammar rule is one small method:
   parse_select(), parse_where(), parse_expr(), and so on.
   "Recursive descent" just means: to parse an OR expression we
   call the AND parser, which calls the NOT parser, which calls
   the comparison parser, and so on down to a single value.

No regular-expression-only hack is used here: the grammar is
real, so "WHERE (a = 1 OR b = 2) AND c = 3" parses with the
parentheses in the right place.

SQL supported in v2 (see README for the full list):
    CREATE TABLE, DROP TABLE, CREATE INDEX, DROP INDEX,
    INSERT, SELECT, UPDATE, DELETE, ALTER TABLE ADD COLUMN,
    BEGIN / COMMIT / ROLLBACK (transactions),
    JOIN (one INNER JOIN), GROUP BY / HAVING, SELECT DISTINCT,
    WHERE (=, !=, <>, <, >, <=, >=, AND, OR, NOT, BETWEEN),
ORDER BY, LIMIT, OFFSET, and COUNT(*)/SUM/AVG/MIN/MAX.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class MiniDBError(Exception):
    """Any user-visible MiniDB error: bad SQL, bad types, bad data."""


# ------------------------------------------------------------------
# Tokens
# ------------------------------------------------------------------

KEYWORDS = {
    "CREATE", "TABLE", "DROP", "INDEX", "ON", "INSERT", "INTO",
    "VALUES", "SELECT", "FROM", "WHERE", "AND", "OR", "NOT",
    "ORDER", "BY", "LIMIT", "OFFSET", "UPDATE", "SET", "DELETE",
    "PRIMARY", "KEY", "NULL", "UNIQUE", "INTEGER", "TEXT", "REAL",
    "BLOB", "ASC", "DESC", "IF", "EXISTS", "COUNT", "SUM", "AVG",
    "MIN", "MAX", "TRUE", "FALSE", "AUTOINCREMENT",
    "BEGIN", "COMMIT", "ROLLBACK", "TRANSACTION", "JOIN", "INNER",
    "GROUP", "HAVING", "DISTINCT", "ALTER", "ADD", "COLUMN",
    "DEFAULT", "USING", "BTREE", "HASH", "BETWEEN",
}


@dataclass
class Token:
    kind: str  # "IDENT", "NUMBER", "STRING", "SYMBOL", "KEYWORD", "EOF"
    value: object
    text: str = ""  # the original text, useful for error messages


class Tokenizer:
    """Split SQL text into tokens.  Knows nothing about grammar."""

    def __init__(self, text: str):
        self.text = text
        self.pos = 0
        self.n = len(text)

    def tokenize(self) -> list[Token]:
        tokens: list[Token] = []
        while self.pos < self.n:
            ch = self.text[self.pos]
            if ch.isspace():
                self.pos += 1
                continue
            # Line comment: -- until end of line.
            if ch == "-" and self._peek(1) == "-":
                while self.pos < self.n and self.text[self.pos] != "\n":
                    self.pos += 1
                continue
            if ch == "'":
                tokens.append(self._string())
                continue
            if ch == '"' or ch == "`":
                tokens.append(self._quoted_ident(ch))
                continue
            if ch.isdigit():
                tokens.append(self._number())
                continue
            if ch.isalpha() or ch == "_":
                tokens.append(self._word())
                continue
            # Two-character symbols first: <= >= != <>
            two = self.text[self.pos : self.pos + 2]
            if two in ("<=", ">=", "!=", "<>"):
                tokens.append(Token("SYMBOL", two, two))
                self.pos += 2
                continue
            if ch == "-" and self._peek(1) != "-":
                tokens.append(Token("SYMBOL", "-", "-"))
                self.pos += 1
                continue
            if ch in "(),;*.=<>":
                tokens.append(Token("SYMBOL", ch, ch))
                self.pos += 1
                continue
            raise MiniDBError(f"Unexpected character {ch!r} in SQL.")
        tokens.append(Token("EOF", None, ""))
        return tokens

    def _peek(self, ahead: int) -> str:
        if self.pos + ahead < self.n:
            return self.text[self.pos + ahead]
        return ""

    def _string(self) -> Token:
        # Single-quoted string.  Two quotes in a row mean one quote.
        self.pos += 1  # skip opening '
        out: list[str] = []
        while self.pos < self.n:
            ch = self.text[self.pos]
            if ch == "'":
                if self._peek(1) == "'":
                    out.append("'")
                    self.pos += 2
                    continue
                self.pos += 1  # closing '
                return Token("STRING", "".join(out), "".join(out))
            out.append(ch)
            self.pos += 1
        raise MiniDBError("Unterminated string literal in SQL.")

    def _quoted_ident(self, quote: str) -> Token:
        self.pos += 1
        start = self.pos
        while self.pos < self.n and self.text[self.pos] != quote:
            self.pos += 1
        if self.pos >= self.n:
            raise MiniDBError("Unterminated quoted identifier in SQL.")
        value = self.text[start : self.pos]
        self.pos += 1
        return Token("IDENT", value, value)

    def _number(self) -> Token:
        start = self.pos
        is_float = False
        while self.pos < self.n and (
            self.text[self.pos].isdigit() or self.text[self.pos] == "."
        ):
            if self.text[self.pos] == ".":
                is_float = True
            self.pos += 1
        text = self.text[start : self.pos]
        try:
            value: object = float(text) if is_float else int(text)
        except ValueError:
            raise MiniDBError(f"Bad number {text!r}.")
        return Token("NUMBER", value, text)

    def _word(self) -> Token:
        start = self.pos
        while self.pos < self.n and (
            self.text[self.pos].isalnum() or self.text[self.pos] == "_"
        ):
            self.pos += 1
        text = self.text[start : self.pos]
        upper = text.upper()
        if upper in KEYWORDS:
            return Token("KEYWORD", upper, text)
        return Token("IDENT", text, text)


# ------------------------------------------------------------------
# AST nodes -- one small class per kind of thing in SQL.
# ------------------------------------------------------------------

@dataclass
class Literal:
    value: object


@dataclass
class ColumnRef:
    name: str  # may be "users.id"; executor strips the table part


@dataclass
class BinaryOp:
    op: str  # AND, OR, =, !=, <, >, <=, >=
    left: object
    right: object


@dataclass
class UnaryOp:
    op: str  # NOT, or "-" for a negative number
    operand: object


@dataclass
class Aggregate:
    func: str  # COUNT, SUM, AVG, MIN, MAX
    arg: object  # ColumnRef, or "*" for COUNT(*)
    is_star: bool = False


@dataclass
class SelectItem:
    expr: object  # ColumnRef, Aggregate, or Literal
    alias: str | None = None


@dataclass
class CreateTableStmt:
    table: str
    columns: list[dict]  # each: name/type/primary_key/not_null/unique


@dataclass
class DropTableStmt:
    table: str
    if_exists: bool = False


@dataclass
class CreateIndexStmt:
    name: str
    table: str
    column: str
    unique: bool = False
    index_type: str = "hash"  # "hash" or "btree"


@dataclass
class BeginStmt:
    pass


@dataclass
class CommitStmt:
    pass


@dataclass
class RollbackStmt:
    pass


@dataclass
class AlterTableStmt:
    table: str
    column: dict  # name/type/primary_key/not_null/unique/default/has_default


@dataclass
class DropIndexStmt:
    name: str
    if_exists: bool = False


@dataclass
class InsertStmt:
    table: str
    columns: list[str] | None  # None means "all columns, in order"
    rows: list[list[object]]  # each row is a list of expressions


@dataclass
class SelectStmt:
    columns: list[SelectItem]
    table: str
    where: object | None = None
    order_by: list[tuple[str, str]] = field(default_factory=list)
    limit: int | None = None
    offset: int | None = None
    distinct: bool = False
    join_table: str | None = None
    join_on: object | None = None
    group_by: list[str] = field(default_factory=list)
    having: object | None = None


@dataclass
class UpdateStmt:
    table: str
    assignments: list[tuple[str, object]]  # (column, expression)
    where: object | None = None


@dataclass
class DeleteStmt:
    table: str
    where: object | None = None


# ------------------------------------------------------------------
# Parser
# ------------------------------------------------------------------

class Parser:
    """Recursive-descent parser over a token list."""

    def __init__(self, tokens: list[Token]):
        self.tokens = tokens
        self.pos = 0

    # -- helpers ----------------------------------------------------

    def peek(self) -> Token:
        return self.tokens[self.pos]

    def next(self) -> Token:
        tok = self.tokens[self.pos]
        if self.pos < len(self.tokens) - 1:
            self.pos += 1
        return tok

    def is_keyword(self, word: str) -> bool:
        tok = self.peek()
        return tok.kind == "KEYWORD" and tok.value == word

    def is_symbol(self, sym: str) -> bool:
        tok = self.peek()
        return tok.kind == "SYMBOL" and tok.value == sym

    def match_keyword(self, word: str) -> bool:
        if self.is_keyword(word):
            self.next()
            return True
        return False

    def match_symbol(self, sym: str) -> bool:
        if self.is_symbol(sym):
            self.next()
            return True
        return False

    def expect_keyword(self, word: str) -> None:
        if not self.match_keyword(word):
            raise MiniDBError(
                f"Expected {word}, got {self.peek().text!r}."
            )

    def expect_symbol(self, sym: str) -> None:
        if not self.match_symbol(sym):
            raise MiniDBError(
                f"Expected {sym!r}, got {self.peek().text!r}."
            )

    def expect_ident(self) -> str:
        tok = self.next()
        # Keywords such as KEY may also be used as a name, e.g. a
        # column called "key" in the _kv table.  Accept them here;
        # the grammar checks for real keywords before calling this.
        if tok.kind not in ("IDENT", "KEYWORD"):
            raise MiniDBError(f"Expected a name, got {tok.text!r}.")
        return str(tok.value)

    # -- entry point ------------------------------------------------

    def parse_statement(self) -> object:
        """Parse one statement.  A trailing ';' is allowed."""
        if self.is_keyword("CREATE"):
            stmt = self._parse_create()
        elif self.is_keyword("ALTER"):
            stmt = self._parse_alter()
        elif self.is_keyword("BEGIN"):
            self.next()
            self.match_keyword("TRANSACTION")
            stmt = BeginStmt()
        elif self.is_keyword("COMMIT"):
            self.next()
            self.match_keyword("TRANSACTION")
            stmt = CommitStmt()
        elif self.is_keyword("ROLLBACK"):
            self.next()
            self.match_keyword("TRANSACTION")
            stmt = RollbackStmt()
        elif self.is_keyword("DROP"):
            stmt = self._parse_drop()
        elif self.is_keyword("INSERT"):
            stmt = self._parse_insert()
        elif self.is_keyword("SELECT"):
            stmt = self._parse_select()
        elif self.is_keyword("UPDATE"):
            stmt = self._parse_update()
        elif self.is_keyword("DELETE"):
            stmt = self._parse_delete()
        else:
            raise MiniDBError(
                f"Unsupported statement starting with {self.peek().text!r}. "
                "MiniDB v2 supports CREATE/DROP TABLE, CREATE/DROP INDEX, "
                "ALTER TABLE, INSERT, SELECT, UPDATE, DELETE, "
                "BEGIN/COMMIT/ROLLBACK, JOIN, GROUP BY."
            )
        self.match_symbol(";")
        if self.peek().kind != "EOF":
            raise MiniDBError(
                f"Unexpected text after statement: {self.peek().text!r}. "
                "Run one statement at a time."
            )
        return stmt

    # -- CREATE / DROP ----------------------------------------------

    def _parse_create(self) -> object:
        self.expect_keyword("CREATE")
        if self.match_keyword("UNIQUE"):
            # CREATE UNIQUE INDEX ...
            self.expect_keyword("INDEX")
            return self._parse_create_index(unique=True)
        if self.is_keyword("TABLE"):
            self.next()
            return self._parse_create_table()
        if self.is_keyword("INDEX"):
            self.next()
            return self._parse_create_index(unique=False)
        raise MiniDBError("CREATE must be followed by TABLE or INDEX.")

    def _parse_create_table(self) -> CreateTableStmt:
        table = self.expect_ident()
        self.expect_symbol("(")
        columns: list[dict] = []
        table_pk: str | None = None
        while True:
            # Table-level constraint: PRIMARY KEY (col) / UNIQUE (col)
            if self.is_keyword("PRIMARY"):
                self.next()
                self.expect_keyword("KEY")
                self.expect_symbol("(")
                table_pk = self.expect_ident()
                self.expect_symbol(")")
            elif self.is_keyword("UNIQUE"):
                self.next()
                self.expect_symbol("(")
                uniq_col = self.expect_ident()
                self.expect_symbol(")")
                # Mark that column UNIQUE once we know it.
                for c in columns:
                    if c["name"].lower() == uniq_col.lower():
                        c["unique"] = True
            else:
                col_name = self.expect_ident()
                tok = self.next()
                if tok.kind != "KEYWORD" or tok.value not in (
                    "INTEGER", "TEXT", "REAL", "BLOB",
                ):
                    raise MiniDBError(
                        f"Expected a type (INTEGER/TEXT/REAL/BLOB) "
                        f"for column {col_name!r}, got {tok.text!r}."
                    )
                col: dict = {
                    "name": col_name,
                    "type": str(tok.value),
                    "primary_key": False,
                    "not_null": False,
                    "unique": False,
                }
                # Column constraints, in any order.
                while True:
                    if self.is_keyword("PRIMARY"):
                        self.next()
                        self.expect_keyword("KEY")
                        col["primary_key"] = True
                    elif self.is_keyword("NOT"):
                        self.next()
                        self.expect_keyword("NULL")
                        col["not_null"] = True
                    elif self.is_keyword("UNIQUE"):
                        self.next()
                        col["unique"] = True
                    elif self.is_keyword("AUTOINCREMENT"):
                        self.next()  # no-op: INTEGER PK already auto-increments
                    else:
                        break
                columns.append(col)
            if self.match_symbol(","):
                continue
            break
        self.expect_symbol(")")
        if table_pk is not None:
            found = False
            for c in columns:
                if c["name"].lower() == table_pk.lower():
                    c["primary_key"] = True
                    found = True
            if not found:
                raise MiniDBError(
                    f"PRIMARY KEY column {table_pk!r} is not in the table."
                )
        if not columns:
            raise MiniDBError("CREATE TABLE needs at least one column.")
        return CreateTableStmt(table=table, columns=columns)

    def _parse_create_index(self, unique: bool) -> CreateIndexStmt:
        name = self.expect_ident()
        self.expect_keyword("ON")
        table = self.expect_ident()
        self.expect_symbol("(")
        column = self.expect_ident()
        self.expect_symbol(")")
        index_type = "hash"
        if self.match_keyword("USING"):
            tok = self.next()
            if tok.kind != "KEYWORD" or tok.value not in ("BTREE", "HASH"):
                raise MiniDBError(
                    "USING must be followed by BTREE or HASH."
                )
            index_type = str(tok.value).lower()
        return CreateIndexStmt(
            name=name, table=table, column=column,
            unique=unique, index_type=index_type,
        )

    def _parse_alter(self) -> AlterTableStmt:
        """ALTER TABLE t ADD COLUMN c TYPE [DEFAULT x] [NOT NULL] [UNIQUE]."""
        self.expect_keyword("ALTER")
        self.expect_keyword("TABLE")
        table = self.expect_ident()
        self.expect_keyword("ADD")
        self.match_keyword("COLUMN")  # ADD COLUMN or just ADD
        col_name = self.expect_ident()
        tok = self.next()
        if tok.kind != "KEYWORD" or tok.value not in (
            "INTEGER", "TEXT", "REAL", "BLOB",
        ):
            raise MiniDBError(
                f"Expected a type (INTEGER/TEXT/REAL/BLOB) for column "
                f"{col_name!r}, got {tok.text!r}."
            )
        col: dict = {
            "name": col_name,
            "type": str(tok.value),
            "primary_key": False,
            "not_null": False,
            "unique": False,
            "default": None,
            "has_default": False,
        }
        while True:
            if self.is_keyword("PRIMARY"):
                raise MiniDBError(
                    "Cannot ADD a PRIMARY KEY column to an existing table."
                )
            if self.is_keyword("NOT"):
                self.next()
                self.expect_keyword("NULL")
                col["not_null"] = True
            elif self.is_keyword("UNIQUE"):
                self.next()
                col["unique"] = True
            elif self.is_keyword("DEFAULT"):
                self.next()
                value = self._parse_primary()
                if not isinstance(value, Literal):
                    raise MiniDBError("DEFAULT needs a literal value.")
                col["default"] = value.value
                col["has_default"] = True
            else:
                break
        return AlterTableStmt(table=table, column=col)

    def _parse_drop(self) -> object:
        self.expect_keyword("DROP")
        if self.is_keyword("TABLE"):
            self.next()
            if_exists = False
            if self.is_keyword("IF"):
                self.next()
                self.expect_keyword("EXISTS")
                if_exists = True
            return DropTableStmt(table=self.expect_ident(), if_exists=if_exists)
        if self.is_keyword("INDEX"):
            self.next()
            if_exists = False
            if self.is_keyword("IF"):
                self.next()
                self.expect_keyword("EXISTS")
                if_exists = True
            return DropIndexStmt(name=self.expect_ident(), if_exists=if_exists)
        raise MiniDBError("DROP must be followed by TABLE or INDEX.")

    # -- INSERT -----------------------------------------------------

    def _parse_insert(self) -> InsertStmt:
        self.expect_keyword("INSERT")
        self.expect_keyword("INTO")
        table = self.expect_ident()
        columns: list[str] | None = None
        if self.match_symbol("("):
            columns = []
            while True:
                columns.append(self.expect_ident())
                if self.match_symbol(","):
                    continue
                break
            self.expect_symbol(")")
        self.expect_keyword("VALUES")
        rows: list[list[object]] = []
        while True:
            self.expect_symbol("(")
            values: list[object] = []
            while True:
                values.append(self._parse_expr())
                if self.match_symbol(","):
                    continue
                break
            self.expect_symbol(")")
            rows.append(values)
            if self.match_symbol(","):
                continue
            break
        return InsertStmt(table=table, columns=columns, rows=rows)

    # -- SELECT -----------------------------------------------------

    def _parse_select(self) -> SelectStmt:
        self.expect_keyword("SELECT")
        distinct = self.match_keyword("DISTINCT")
        columns: list[SelectItem] = []
        while True:
            if self.is_symbol("*"):
                self.next()
                columns.append(SelectItem(expr=ColumnRef("*")))
            elif self.peek().kind == "KEYWORD" and str(self.peek().value) in (
                "COUNT", "SUM", "AVG", "MIN", "MAX",
            ):
                columns.append(SelectItem(expr=self._parse_aggregate()))
            else:
                # A plain column, possibly table.column
                name = self.expect_ident()
                if self.match_symbol("."):
                    name = name + "." + self.expect_ident()
                columns.append(SelectItem(expr=ColumnRef(name)))
            if self.match_symbol(","):
                continue
            break
        self.expect_keyword("FROM")
        table = self.expect_ident()
        join_table: str | None = None
        join_on = None
        if self.is_keyword("JOIN") or (
            self.is_keyword("INNER") and self.tokens[self.pos + 1].kind == "KEYWORD"
            and self.tokens[self.pos + 1].value == "JOIN"
        ):
            if self.is_keyword("INNER"):
                self.next()
            self.expect_keyword("JOIN")
            join_table = self.expect_ident()
            self.expect_keyword("ON")
            join_on = self._parse_expr()
        where = None
        if self.match_keyword("WHERE"):
            where = self._parse_expr()
        group_by: list[str] = []
        having = None
        if self.match_keyword("GROUP"):
            self.expect_keyword("BY")
            while True:
                name = self.expect_ident()
                if self.match_symbol("."):
                    name = name + "." + self.expect_ident()
                group_by.append(name)
                if self.match_symbol(","):
                    continue
                break
            if self.match_keyword("HAVING"):
                having = self._parse_expr()
        order_by: list[tuple[str, str]] = []
        if self.match_keyword("ORDER"):
            self.expect_keyword("BY")
            while True:
                name = self.expect_ident()
                if self.match_symbol("."):
                    name = name + "." + self.expect_ident()
                direction = "ASC"
                if self.is_keyword("ASC"):
                    self.next()
                elif self.is_keyword("DESC"):
                    self.next()
                    direction = "DESC"
                order_by.append((name, direction))
                if self.match_symbol(","):
                    continue
                break
        limit: int | None = None
        offset: int | None = None
        if self.match_keyword("LIMIT"):
            tok = self.next()
            if tok.kind != "NUMBER" or isinstance(tok.value, float):
                raise MiniDBError("LIMIT needs a whole number.")
            limit = int(tok.value)  # type: ignore[arg-type]
            if self.match_keyword("OFFSET"):
                tok = self.next()
                if tok.kind != "NUMBER":
                    raise MiniDBError("OFFSET needs a whole number.")
                offset = int(tok.value)  # type: ignore[arg-type]
        elif self.match_keyword("OFFSET"):
            tok = self.next()
            if tok.kind != "NUMBER":
                raise MiniDBError("OFFSET needs a whole number.")
            offset = int(tok.value)  # type: ignore[arg-type]
        return SelectStmt(
            columns=columns, table=table, where=where,
            order_by=order_by, limit=limit, offset=offset,
            distinct=distinct, join_table=join_table, join_on=join_on,
            group_by=group_by, having=having,
        )

    def _parse_aggregate(self) -> Aggregate:
        func = str(self.next().value)  # COUNT / SUM / AVG / MIN / MAX
        self.expect_symbol("(")
        if self.is_symbol("*"):
            self.next()
            self.expect_symbol(")")
            if func != "COUNT":
                raise MiniDBError(f"{func}(*) is not valid; only COUNT(*) is.")
            return Aggregate(func=func, arg=ColumnRef("*"), is_star=True)
        name = self.expect_ident()
        if self.match_symbol("."):
            name = name + "." + self.expect_ident()
        self.expect_symbol(")")
        return Aggregate(func=func, arg=ColumnRef(name))

    # -- UPDATE / DELETE --------------------------------------------

    def _parse_update(self) -> UpdateStmt:
        self.expect_keyword("UPDATE")
        table = self.expect_ident()
        self.expect_keyword("SET")
        assignments: list[tuple[str, object]] = []
        while True:
            col = self.expect_ident()
            self.expect_symbol("=")
            assignments.append((col, self._parse_expr()))
            if self.match_symbol(","):
                continue
            break
        where = None
        if self.match_keyword("WHERE"):
            where = self._parse_expr()
        return UpdateStmt(table=table, assignments=assignments, where=where)

    def _parse_delete(self) -> DeleteStmt:
        self.expect_keyword("DELETE")
        self.expect_keyword("FROM")
        table = self.expect_ident()
        where = None
        if self.match_keyword("WHERE"):
            where = self._parse_expr()
        return DeleteStmt(table=table, where=where)

    # -- expressions: OR > AND > NOT > comparison > primary --------

    def _parse_expr(self) -> object:
        return self._parse_or()

    def _parse_or(self) -> object:
        left = self._parse_and()
        while self.is_keyword("OR"):
            self.next()
            left = BinaryOp(op="OR", left=left, right=self._parse_and())
        return left

    def _parse_and(self) -> object:
        left = self._parse_not()
        while self.is_keyword("AND"):
            self.next()
            left = BinaryOp(op="AND", left=left, right=self._parse_not())
        return left

    def _parse_not(self) -> object:
        if self.is_keyword("NOT"):
            self.next()
            return UnaryOp(op="NOT", operand=self._parse_not())
        return self._parse_comparison()

    def _parse_comparison(self) -> object:
        left = self._parse_primary()
        # col BETWEEN low AND high  ->  col >= low AND col <= high
        if self.is_keyword("BETWEEN"):
            self.next()
            low = self._parse_primary()
            self.expect_keyword("AND")
            high = self._parse_primary()
            return BinaryOp(
                op="AND",
                left=BinaryOp(op=">=", left=left, right=low),
                right=BinaryOp(op="<=", left=left, right=high),
            )
        # IS NULL / IS NOT NULL
        if self.is_keyword("IS"):
            self.next()
            negated = self.match_keyword("NOT")
            self.expect_keyword("NULL")
            # Represent as: left = NULL  (or != NULL when negated)
            op = "!=" if negated else "="
            return BinaryOp(op=op, left=left, right=Literal(None))
        tok = self.peek()
        if tok.kind == "SYMBOL" and tok.value in ("=", "!=", "<>", "<", ">", "<=", ">="):
            self.next()
            op = "<>" if tok.value == "<>" else str(tok.value)
            if op == "<>":
                op = "!="
            right = self._parse_primary()
            return BinaryOp(op=op, left=left, right=right)
        return left

    def _parse_primary(self) -> object:
        tok = self.peek()
        if tok.kind == "KEYWORD" and str(tok.value) in (
            "COUNT", "SUM", "AVG", "MIN", "MAX",
        ):
            return self._parse_aggregate()
        if self.is_symbol("("):
            self.next()
            expr = self._parse_expr()
            self.expect_symbol(")")
            return expr
        if self.is_symbol("-"):
            self.next()
            value = self._parse_primary()
            return UnaryOp(op="-", operand=value)
        if tok.kind == "NUMBER":
            self.next()
            return Literal(tok.value)
        if tok.kind == "STRING":
            self.next()
            return Literal(tok.value)
        if tok.kind == "KEYWORD":
            if tok.value == "NULL":
                self.next()
                return Literal(None)
            if tok.value == "TRUE":
                self.next()
                return Literal(True)
            if tok.value == "FALSE":
                self.next()
                return Literal(False)
            if tok.value in ("AND", "OR", "NOT", "IS"):
                raise MiniDBError(f"Unexpected keyword {tok.text!r} in expression.")
            # Any other keyword may be a column name, e.g. a column
            # called "key".  Treat it as a column reference.
            self.next()
            return ColumnRef(str(tok.value))
        if tok.kind == "IDENT":
            self.next()
            name = str(tok.value)
            if self.match_symbol("."):
                name = name + "." + self.expect_ident()
            return ColumnRef(name)
        raise MiniDBError(f"Unexpected token {tok.text!r} in expression.")


def parse(sql: str) -> object:
    """Parse one SQL statement into an AST node.

    Raises MiniDBError on bad SQL.  Empty input raises too.
    """
    if sql is None or not str(sql).strip():
        raise MiniDBError("Empty SQL statement.")
    tokens = Tokenizer(str(sql)).tokenize()
    return Parser(tokens).parse_statement()
