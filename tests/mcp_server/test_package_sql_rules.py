"""Regeln für SQL im Paket hoffmann_data (F07): nur Lesen, nie Eingabe im SQL-Text.

Der Datenservice darf nur lesen (Auftrag 5.1). Die Datenbankrolle setzt das durch; diese Tests sichern
zusätzlich den Quelltext ab, damit auch ein Fehler im Code nicht zu Schreib-SQL oder zu SQL-Injektion führt:

1. In den Zeichenketten des Pakets (Docstrings ausgenommen) steht kein Schlüsselwort einer Schreib- oder
   Strukturanweisung. Einzige Ausnahme: In db.py dürfen die Rechtenamen INSERT, UPDATE, DELETE, TRUNCATE
   und CREATE sowie die Kurzbezeichnung create-im-schema der Rollenprüfung als eigene, exakt gleiche
   Zeichenketten stehen (Startprüfung der Rolle mit has_table_privilege und has_schema_privilege, Etappe 7);
   sie dürfen nicht Teil eines SQL-Satzes sein.
2. Wert und Eingabe werden nie in SQL-Text eingesetzt: keine f-Strings, kein %-Operator, kein Plus und kein
   .format auf SQL-Zeichenketten. Erlaubt ist nur psycopg.sql.SQL("...").format(sql.Identifier("fester Name")).
   Werte gehen immer als Parameter an die Abfrage.

Die Prüfer sind Funktionen, die ein eigener Selbsttest an Beispielen erprobt. Sonst wäre die Prüfung des
Pakets vor der Implementierung wirkungslos grün.
"""

import ast
import re
from pathlib import Path

import pytest
from mcp_testkit import MCP_SERVER_DIR

PACKAGE = Path(MCP_SERVER_DIR) / "hoffmann_data"
DB_MODULE = PACKAGE / "db.py"

FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|GRANT|COPY|CREATE|MERGE)\b", re.IGNORECASE
)
# Die Rechtenamen der Rollenprüfung und die Kurzbezeichnung create-im-schema: nur in db.py, nur als exakt
# gleiche Zeichenkette (Parameter der Abfragen bzw. Text des Fehlers), nie in einem SQL-Satz
PRIVILEGE_NAMES = frozenset(
    {"INSERT", "UPDATE", "DELETE", "TRUNCATE", "CREATE", "create-im-schema"}
)
# Eine Zeichenkette gilt als SQL, wenn sie eine Abfrage oder eine Sitzungseinstellung enthält
SQL_LIKE = re.compile(r"\b(SELECT|SET\s+LOCAL)\b", re.IGNORECASE)


def _docstring_ids(tree: ast.AST) -> set[int]:
    ids: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if (
                isinstance(first, ast.Expr)
                and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)
            ):
                ids.add(id(first.value))
    return ids


def string_constants(tree: ast.AST) -> list[str]:
    """Alle Zeichenketten außer Docstrings (auch die festen Teile von f-Strings)."""
    docstrings = _docstring_ids(tree)
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def forbidden_words(source: str, *, allow_privilege_names: bool = False) -> list[str]:
    """Schlüsselwörter von Schreib- und Strukturanweisungen in den Zeichenketten des Quelltexts."""
    found: list[str] = []
    for text in string_constants(ast.parse(source)):
        if allow_privilege_names and text in PRIVILEGE_NAMES:
            continue
        found.extend(match.group(0).upper() for match in FORBIDDEN.finditer(text))
    return found


def _is_sql_text(node: ast.AST, sql_names: set[str]) -> bool:
    if isinstance(node, ast.Constant):
        return isinstance(node.value, str) and bool(SQL_LIKE.search(node.value))
    if isinstance(node, ast.Name):
        return node.id in sql_names
    return False


def _is_sql_call(node: ast.AST) -> bool:
    """`SQL(...)` oder `sql.SQL(...)` von psycopg.sql."""
    if not isinstance(node, ast.Call):
        return False
    func = node.func
    return (isinstance(func, ast.Name) and func.id == "SQL") or (
        isinstance(func, ast.Attribute) and func.attr == "SQL"
    )


def _is_fixed_identifier(node: ast.AST) -> bool:
    """`Identifier("fester Name")` oder `sql.Identifier("fester Name")` mit Zeichenketten als Argumenten."""
    if not isinstance(node, ast.Call) or node.keywords or not node.args:
        return False
    func = node.func
    named = (isinstance(func, ast.Name) and func.id == "Identifier") or (
        isinstance(func, ast.Attribute) and func.attr == "Identifier"
    )
    return named and all(
        isinstance(arg, ast.Constant) and isinstance(arg.value, str) for arg in node.args
    )


def _sql_constant_names(tree: ast.AST) -> set[str]:
    """Namen, an die eine SQL-Zeichenkette (auch als SQL(...)) gebunden wird."""
    names: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Assign):
            targets, value = node.targets, node.value
        elif isinstance(node, ast.AnnAssign) and node.value is not None:
            targets, value = [node.target], node.value
        else:
            continue
        wrapped = value.args[0] if _is_sql_call(value) and value.args else value  # type: ignore[attr-defined]
        if _is_sql_text(wrapped, set()):
            names.update(t.id for t in targets if isinstance(t, ast.Name))
    return names


def unsafe_formatting(source: str) -> list[str]:
    """Stellen, an denen SQL-Text durch Einsetzen von Werten entsteht. Leer heißt: nichts gefunden."""
    tree = ast.parse(source)
    names = _sql_constant_names(tree)
    findings: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.JoinedStr):
            parts_are_sql = any(_is_sql_text(part, names) for part in node.values)
            uses_sql_name = any(
                isinstance(part, ast.FormattedValue) and _is_sql_text(part.value, names)
                for part in node.values
            )
            if parts_are_sql or uses_sql_name:
                findings.append("f-string")
        elif isinstance(node, ast.BinOp) and isinstance(node.op, ast.Mod | ast.Add):
            if _is_sql_text(node.left, names) or _is_sql_text(node.right, names):
                findings.append(
                    "%-Operator" if isinstance(node.op, ast.Mod) else "Verkettung mit +"
                )
        elif (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Attribute)
            and node.func.attr == "format"
        ):
            target = node.func.value
            if _is_sql_call(target):
                fixed_text = bool(target.args) and isinstance(target.args[0], ast.Constant)  # type: ignore[attr-defined]
                fixed_names = (
                    all(_is_fixed_identifier(arg) for arg in node.args) and not node.keywords
                )
                if not (fixed_text and fixed_names):
                    findings.append("SQL(...).format mit anderem als festen Namen")
            elif _is_sql_text(target, names):
                findings.append(".format auf einer SQL-Zeichenkette")
    return findings


def _sources() -> list[Path]:
    sources = sorted(PACKAGE.rglob("*.py"))
    assert sources, "mcp-server/hoffmann_data enthält noch keinen Code"
    return sources


# Selbsttests der Prüfer


@pytest.mark.parametrize(
    "snippet",
    [
        'q = "INSERT INTO products DEFAULT VALUES"',
        'q = "update products set name = %s"',
        'q = "DELETE FROM products"',
        'q = "DROP TABLE products"',
        'q = "ALTER ROLE x PASSWORD y"',
        'q = "TRUNCATE products"',
        'q = "GRANT SELECT ON products TO x"',
        'q = "COPY products TO STDOUT"',
        'q = "CREATE TABLE t (a int)"',
        'q = "MERGE INTO products USING x ON true"',
        'q = f"SELECT 1; DROP TABLE {name}"',
        'cur.execute("SELECT 1; delete from products")',
    ],
)
def test_checker_detects_write_keywords(snippet: str) -> None:
    assert forbidden_words(snippet), snippet


@pytest.mark.parametrize(
    "snippet",
    [
        'q = "SELECT article_number, name FROM products WHERE is_active"',
        'q = "SELECT created_by, created_at FROM activities"',
        'name = "create_lead"',
        'text = "Neuen Lead anlegen, nur nach Freigabe"',
        'def f():\n    """Wirft bei INSERT und DELETE nie."""\n    return 1',
        '"""Modul: kein UPDATE, kein DROP."""\nx = 1',
        'class A:\n    """INSERT"""\n    x = 1',
        "# INSERT INTO products\nx = 1",
    ],
)
def test_checker_ignores_harmless_text_and_docstrings(snippet: str) -> None:
    assert forbidden_words(snippet) == [], snippet


def test_checker_allows_privilege_names_only_when_asked() -> None:
    snippet = 'PRIVILEGES = ("INSERT", "UPDATE", "DELETE", "TRUNCATE", "CREATE")'
    assert forbidden_words(snippet)
    assert forbidden_words(snippet, allow_privilege_names=True) == []
    label = 'CREATE_FAILURE = "create-im-schema"'
    assert forbidden_words(label)
    assert forbidden_words(label, allow_privilege_names=True) == []
    in_sentence = 'q = "INSERT INTO products DEFAULT VALUES"'
    assert forbidden_words(in_sentence, allow_privilege_names=True)
    # Nur die exakt gleiche Zeichenkette ist erlaubt, nie ein Satz oder eine Anweisung damit
    assert forbidden_words('q = "CREATE TABLE t (a int)"', allow_privilege_names=True)
    assert forbidden_words('label = "create-im-schema extra"', allow_privilege_names=True)


@pytest.mark.parametrize(
    "snippet",
    [
        "q = f\"SELECT * FROM products WHERE name = '{name}'\"",
        "q = \"SELECT * FROM products WHERE name = '%s'\" % name",
        'q = "SELECT * FROM products WHERE name = \'" + name + "\'"',
        "q = \"SELECT * FROM products WHERE name = '{}'\".format(name)",
        'Q = "SELECT * FROM products WHERE name = %s"\nq = Q % name',
        'Q = "SELECT * FROM products WHERE name = {}"\nq = Q.format(name)',
        'Q = "SELECT * FROM products"\nq = f"{Q} WHERE name = \'{name}\'"',
        'Q = "SELECT * FROM products"\nq = Q + " WHERE x"',
        'q = SQL("SELECT * FROM {}").format(name)',
        'q = sql.SQL("SELECT * FROM {}").format(sql.Identifier(name))',
        'q = sql.SQL(text).format(sql.Identifier("products"))',
        'q = sql.SQL("SELECT {}").format(sql.Literal(value))',
        'q = sql.SQL("SELECT * FROM {}").format(table=sql.Identifier("products"))',
        "q = \"SET LOCAL statement_timeout = '%s'\" % seconds",
    ],
)
def test_checker_detects_values_put_into_sql_text(snippet: str) -> None:
    assert unsafe_formatting(snippet), snippet


@pytest.mark.parametrize(
    "snippet",
    [
        'q = "SELECT * FROM products WHERE name = %(name)s"\ncur.execute(q, {"name": name})',
        'cur.execute("SELECT * FROM products WHERE article_number = %s", (number,))',
        'q = sql.SQL("SELECT * FROM {}").format(sql.Identifier("products"))',
        'q = SQL("SELECT * FROM {} ORDER BY {}").format(Identifier("products"), Identifier("name"))',
        'message = f"Fehler {code}"',
        'message = "Wert %s" % value',
        'message = "Hallo {}".format(name)',
        'text = "Eingabe zu lang: " + str(n)',
    ],
)
def test_checker_accepts_parameters_and_fixed_identifiers(snippet: str) -> None:
    assert unsafe_formatting(snippet) == [], snippet


# Das Paket


def test_package_sql_has_no_write_or_structure_keywords() -> None:
    offenders: dict[str, list[str]] = {}
    for source in _sources():
        found = forbidden_words(
            source.read_text(encoding="utf-8"), allow_privilege_names=source == DB_MODULE
        )
        if found:
            offenders[str(source.relative_to(PACKAGE))] = found
    assert not offenders, f"Schlüsselwörter in Zeichenketten: {offenders}"


def test_package_never_puts_values_into_sql_text() -> None:
    offenders = {
        str(source.relative_to(PACKAGE)): findings
        for source in _sources()
        if (findings := unsafe_formatting(source.read_text(encoding="utf-8")))
    }
    assert not offenders, f"SQL-Text durch Einsetzen gebaut: {offenders}"


def test_package_contains_select_statements_the_rules_apply_to() -> None:
    """Gegenprobe: Die Prüfungen oben sehen echte Abfragen. Sonst wären sie ohne Wirkung."""
    selects = [
        text
        for source in _sources()
        for text in string_constants(ast.parse(source.read_text(encoding="utf-8")))
        if re.search(r"\bSELECT\b", text, re.IGNORECASE)
    ]
    assert selects, "kein SELECT im Paket: Die Lese-Werkzeuge fehlen noch"
