"""Tests für Schreiben, rabatte.md und Einstiegspunkt des Seed-Moduls (F05).

Ohne Datenbank (laufen überall, soweit psycopg installiert ist): json_dumps_exact, render_rabatte,
write_rabatte, Drift-Test von data/richtlinien/rabatte.md, Datenfehler berühren die Verbindung nie,
Einstiegspunkt und Rückgabecodes, Quelltexte.

Mit Datenbank (nur mit TEST_DATABASE_URL, sonst übersprungen): Befüllen, Löschschutz, nur die sechs
Tabellen, kein CASCADE, Atomarität, Werte, Fehlermeldungen. Jeder Test arbeitet in einem leeren
Schema test_<zufall> in einer äußeren Transaktion, die am Ende zurückgerollt wird. Dateien werden
nur in tmp_path geschrieben, nie in data/richtlinien/.
"""

import ast
import dataclasses
import json
import re
import traceback
from collections import Counter
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest

from db.seed import rabatte
from db.seed.guard import RESET_TABLES, SeedGuardError, Target, parse_target
from db.seed.loader import SeedDataError
from db.seed.rabatte import GENERATED_LINE, render_rabatte, write_rabatte

ROOT = Path(__file__).resolve().parents[2]
SEED_DIR = ROOT / "db" / "seed"
DATA_DIR = ROOT / "data" / "stammdaten"
SCHEMA_SQL = ROOT / "db" / "schema.sql"
RABATTE_PATH = ROOT / "data" / "richtlinien" / "rabatte.md"

TARGET = Target(host="db.example.com", dbname="postgres")
URL = "postgresql://u:p@db.example.com/postgres"


@pytest.fixture
def writer():
    return pytest.importorskip("db.seed.writer")


@pytest.fixture
def seed_main():
    return pytest.importorskip("db.seed.__main__")


# json_dumps_exact


def test_json_dumps_exact_writes_decimal_as_number(writer):
    assert writer.json_dumps_exact(Decimal("0.75")) == "0.75"
    assert writer.json_dumps_exact(Decimal("890.0")) == "890.0"
    assert writer.json_dumps_exact(Decimal("1E+2")) == "100"
    value = {"a": Decimal("0.75"), "b": [1, True, None, 'ä"x'], "c": {"d": Decimal("-3.50")}}
    assert json.loads(writer.json_dumps_exact(value), parse_float=Decimal) == value


def test_json_dumps_exact_keeps_the_scale_without_float(writer):
    text = writer.json_dumps_exact({"x": Decimal("0.1000000000000000055511151231257827")})
    assert text == '{"x":0.1000000000000000055511151231257827}'


@pytest.mark.parametrize("value", [0.75, object(), {"a": {1.5}}])
def test_json_dumps_exact_rejects_float_and_unknown_types(writer, value):
    with pytest.raises(TypeError):
        writer.json_dumps_exact(value)


@pytest.mark.parametrize("value", [Decimal("NaN"), Decimal("Infinity")])
def test_json_dumps_exact_rejects_non_finite_numbers(writer, value):
    with pytest.raises(ValueError):
        writer.json_dumps_exact(value)


# render_rabatte


def _rows(text):
    lines = text.splitlines()
    return [
        line for line in lines if line.startswith("| ") and not line.startswith("| Kundenstatus")
    ]


def _cells(row):
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", row)[1:-1]]


def test_render_is_deterministic_and_sorted(real_data):
    text = render_rabatte(real_data)
    assert render_rabatte(real_data) == text
    reordered = dataclasses.replace(
        real_data, discount_rules=tuple(reversed(real_data.discount_rules))
    )
    assert render_rabatte(reordered) == text
    rows = _rows(text)
    assert len(rows) == len(real_data.discount_rules)
    keys = [
        (c[0].replace("alle", ""), c[1].replace("alle", ""), int(c[2])) for c in map(_cells, rows)
    ]
    assert keys == sorted(keys)
    assert _cells(rows[0])[:3] == ["alle", "alle", "1"]


def test_render_first_line_and_hint(real_data):
    text = render_rabatte(real_data)
    assert text.splitlines()[0] == GENERATED_LINE
    assert GENERATED_LINE.startswith("<!-- GENERIERT aus data/stammdaten/discount_rules.json")
    assert "Nicht von Hand ändern" in GENERATED_LINE
    assert "interne Obergrenzen" in text
    assert "nie in einem Antwortentwurf" in text
    assert "Platzhalter" in text
    assert text.endswith("|\n")
    assert not text.endswith("\n\n")
    assert "\r" not in text


def test_render_formats_percent_with_comma_and_escapes_pipes(real_data):
    rule = dataclasses.replace(
        real_data.discount_rules[0],
        max_discount_percent=Decimal("2.5"),
        description="a|b",
    )
    zero = dataclasses.replace(
        real_data.discount_rules[1], max_discount_percent=Decimal("0"), description="null"
    )
    data = dataclasses.replace(real_data, discount_rules=(rule, zero))
    rows = _rows(render_rabatte(data))
    assert len(rows) == 2
    assert _cells(rows[0])[3] == "2,50"
    assert _cells(rows[1])[3] == "0,00"
    assert "a\\|b" in rows[0]
    assert len(_cells(rows[0])) == 5


# write_rabatte


def test_write_rabatte_creates_and_replaces_atomically(tmp_path):
    path = tmp_path / "rabatte.md"
    write_rabatte(path, "alt\n")
    assert path.read_bytes() == b"alt\n"
    write_rabatte(path, "neu äöü\n")
    assert path.read_bytes() == "neu äöü\n".encode()
    assert [item.name for item in tmp_path.iterdir()] == ["rabatte.md"]


@pytest.mark.parametrize("function", ["replace", "fsync"])
def test_write_rabatte_failure_leaves_no_files_and_keeps_the_old_one(
    tmp_path, monkeypatch, function
):
    path = tmp_path / "rabatte.md"
    write_rabatte(path, "alt\n")

    def boom(*args, **kwargs):
        raise OSError("kaputt")

    monkeypatch.setattr(rabatte.os, function, boom)
    with pytest.raises(OSError):
        write_rabatte(path, "neu\n")
    monkeypatch.undo()
    assert path.read_text(encoding="utf-8") == "alt\n"
    assert [item.name for item in tmp_path.iterdir()] == ["rabatte.md"]


def test_write_rabatte_failure_without_old_file_leaves_nothing(tmp_path, monkeypatch):
    def boom(*args, **kwargs):
        raise OSError("kaputt")

    monkeypatch.setattr(rabatte.os, "replace", boom)
    with pytest.raises(OSError):
        write_rabatte(tmp_path / "rabatte.md", "neu\n")
    monkeypatch.undo()
    assert list(tmp_path.iterdir()) == []


def test_write_rabatte_into_missing_folder_fails_cleanly(tmp_path):
    with pytest.raises(OSError):
        write_rabatte(tmp_path / "fehlt" / "rabatte.md", "x\n")
    assert list(tmp_path.iterdir()) == []


def test_rabatte_md_matches_the_generated_text(real_data):
    hint = "python -m db.seed --nur-rabatte ausführen"
    if not RABATTE_PATH.exists():
        pytest.fail(f"data/richtlinien/rabatte.md fehlt. Bitte {hint}.")
    actual = RABATTE_PATH.read_text(encoding="utf-8").replace("\r\n", "\n")
    assert actual == render_rabatte(real_data), f"data/richtlinien/rabatte.md weicht ab. {hint}."


# Datenfehler und Vorbedingungen berühren die Verbindung nie


def test_data_error_never_touches_the_connection(writer, raw, write_raw, exploding_conn, tmp_path):
    del raw["products"][0]["name"]
    directory = write_raw(raw)
    lines: list[str] = []
    with pytest.raises(SeedDataError):
        writer.run(exploding_conn, TARGET, directory, SCHEMA_SQL, tmp_path / "r", "x", lines.append)
    assert lines == []
    assert not (tmp_path / "r").exists()


def test_check_error_never_touches_the_connection(writer, raw, write_raw, exploding_conn, tmp_path):
    raw["activities"][0]["article_number"] = "FB-9999"
    directory = write_raw(raw)
    with pytest.raises(SeedDataError) as excinfo:
        writer.run(
            exploding_conn, TARGET, directory, SCHEMA_SQL, tmp_path / "r", "db.example.com", print
        )
    assert "Prüfung 4" in str(excinfo.value)


def test_missing_schema_file_is_reported_without_touching_the_connection(
    writer, exploding_conn, tmp_path
):
    with pytest.raises(writer.SeedWriteError) as excinfo:
        writer.run(exploding_conn, TARGET, DATA_DIR, tmp_path / "fehlt.sql", tmp_path, None, print)
    assert str(excinfo.value) == "db/schema.sql konnte nicht gelesen werden (FileNotFoundError)."
    assert excinfo.value.__context__ is None


def test_connection_without_autocommit_and_transaction_is_rejected(writer, tmp_path):
    psycopg = pytest.importorskip("psycopg")
    idle = psycopg.pq.TransactionStatus.IDLE
    conn = SimpleNamespace(autocommit=False, info=SimpleNamespace(transaction_status=idle))
    with pytest.raises(writer.SeedWriteError) as excinfo:
        writer.run(conn, TARGET, DATA_DIR, SCHEMA_SQL, tmp_path, "db.example.com", print)
    assert str(excinfo.value) == writer.AUTOCOMMIT_MESSAGE


def test_wrong_effective_host_is_rejected_before_any_query(writer, tmp_path):
    conn = SimpleNamespace(autocommit=True, info=SimpleNamespace(host="anderer.example"))
    lines: list[str] = []
    with pytest.raises(SeedGuardError):
        writer.run(conn, TARGET, DATA_DIR, SCHEMA_SQL, tmp_path, "db.example.com", lines.append)
    assert lines == []
    assert list(tmp_path.iterdir()) == []


# Einstiegspunkt


class FakeConnection:
    def __init__(self):
        self.closed = False

    def close(self):
        self.closed = True


def _fail_connect(*args, **kwargs):
    raise AssertionError("psycopg.connect darf hier nicht aufgerufen werden")


def test_main_without_database_url_ends_with_code_2(seed_main, capsys):
    assert seed_main.main([], {}) == 2
    captured = capsys.readouterr()
    assert captured.err == "DATABASE_URL fehlt oder ist leer.\n"
    assert captured.out == ""


def test_main_with_overriding_environment_ends_with_code_2(seed_main, monkeypatch, capsys):
    monkeypatch.setattr(seed_main.psycopg, "connect", _fail_connect)
    environ = {"DATABASE_URL": URL, "PGHOSTADDR": "192.0.2.1"}
    assert seed_main.main([], environ) == 2
    assert "PGHOSTADDR" in capsys.readouterr().err


def test_main_data_error_ends_with_code_1_before_connecting(
    seed_main, raw, write_raw, monkeypatch, capsys
):
    del raw["products"][0]["name"]
    monkeypatch.setattr(seed_main, "DATA_DIR", write_raw(raw))
    monkeypatch.setattr(seed_main.psycopg, "connect", _fail_connect)
    assert seed_main.main([], {"DATABASE_URL": URL}) == 1
    assert 'Pflichtfeld "name" fehlt' in capsys.readouterr().err


def test_main_connection_error_ends_with_code_3_without_library_text(
    seed_main, monkeypatch, capsys
):
    def boom(*args, **kwargs):
        raise RuntimeError("Passwort Geheim123 und Host intern.example")

    monkeypatch.setattr(seed_main.psycopg, "connect", boom)
    assert seed_main.main([], {"DATABASE_URL": URL}) == 3
    captured = capsys.readouterr()
    assert captured.err == "Verbindung zur Datenbank fehlgeschlagen. (RuntimeError)\n"
    assert "Geheim123" not in captured.out + captured.err
    assert "intern.example" not in captured.out + captured.err


def test_main_passes_the_url_unchanged_with_autocommit_and_timeout(seed_main, monkeypatch):
    calls = []

    def record(*args, **kwargs):
        calls.append((args, kwargs))
        raise RuntimeError("x")

    monkeypatch.setattr(seed_main.psycopg, "connect", record)
    url = "postgresql://benutzer:pw%23x@Db.Example.com:5432/postgres?sslmode=require"
    seed_main.main([], {"DATABASE_URL": url})
    assert calls == [((url,), {"autocommit": True, "connect_timeout": 10})]


@pytest.mark.parametrize(
    ("exception", "code"),
    [("SeedGuardError", 2), ("SeedDataError", 1), ("SeedWriteError", 3)],
)
def test_main_maps_errors_to_codes_and_closes_the_connection(
    seed_main, writer, monkeypatch, capsys, exception, code
):
    fake = FakeConnection()
    errors = {
        "SeedGuardError": SeedGuardError("Schutz"),
        "SeedDataError": SeedDataError(["Daten"]),
        "SeedWriteError": writer.SeedWriteError("Schreiben"),
    }

    def failing_run(*args, **kwargs):
        raise errors[exception]

    monkeypatch.setattr(seed_main.psycopg, "connect", lambda *a, **k: fake)
    monkeypatch.setattr(seed_main, "run", failing_run)
    assert seed_main.main([], {"DATABASE_URL": URL}) == code
    assert fake.closed
    assert capsys.readouterr().err.strip() in {"Schutz", "Daten", "Schreiben"}


def test_main_success_prints_counts_and_passes_the_confirmation(seed_main, monkeypatch, capsys):
    fake = FakeConnection()
    seen = {}

    def fake_run(conn, target, data_dir, schema_sql, richtlinien_dir, confirm, out):
        seen.update(conn=conn, target=target, confirm=confirm)
        out("Ausgabe vor dem Löschen")
        return {"products": 40}

    monkeypatch.setattr(seed_main.psycopg, "connect", lambda *a, **k: fake)
    monkeypatch.setattr(seed_main, "run", fake_run)
    environ = {"DATABASE_URL": URL, "SEED_CONFIRM_RESET": "db.example.com"}
    assert seed_main.main([], environ) == 0
    out = capsys.readouterr().out
    assert "Ausgabe vor dem Löschen" in out
    assert "products: 40" in out
    assert seen == {"conn": fake, "target": parse_target(URL, {}), "confirm": "db.example.com"}
    assert fake.closed


def test_main_only_rabatte_needs_no_environment_and_no_database(
    seed_main, real_data, monkeypatch, tmp_path, capsys
):
    folder = tmp_path / "richtlinien"
    folder.mkdir()
    monkeypatch.setattr(seed_main, "RICHTLINIEN_DIR", folder)
    monkeypatch.setattr(seed_main.psycopg, "connect", _fail_connect)
    assert seed_main.main(["--nur-rabatte"], {}) == 0
    assert (folder / "rabatte.md").read_text(encoding="utf-8") == render_rabatte(real_data)
    assert "rabatte.md geschrieben" in capsys.readouterr().out


def test_main_only_rabatte_with_data_error_ends_with_code_1(
    seed_main, raw, write_raw, monkeypatch, tmp_path
):
    raw["customers"][0]["staus"] = "existing"
    folder = tmp_path / "richtlinien"
    folder.mkdir()
    monkeypatch.setattr(seed_main, "DATA_DIR", write_raw(raw))
    monkeypatch.setattr(seed_main, "RICHTLINIEN_DIR", folder)
    assert seed_main.main(["--nur-rabatte"], {}) == 1
    assert list(folder.iterdir()) == []


@pytest.mark.parametrize("argv", [["--foo"], ["-h"], ["x"], ["--nur-rabatte", "--foo"]])
def test_main_unknown_options_end_with_code_2(seed_main, capsys, argv):
    assert seed_main.main(argv, {}) == 2
    assert capsys.readouterr().err == seed_main.USAGE_MESSAGE + "\n"


# Quelltexte


def test_only_main_reads_the_environment_and_never_the_test_variable():
    main_text = (SEED_DIR / "__main__.py").read_text(encoding="utf-8")
    assert "TEST_DATABASE_URL" not in main_text
    assert "load_dotenv" not in main_text
    for name in ("writer", "rabatte", "loader", "checks", "guard"):
        text = (SEED_DIR / f"{name}.py").read_text(encoding="utf-8")
        assert "os.environ" not in text
        assert "getenv" not in text
        assert "TEST_DATABASE_URL" not in text


def _string_literals(path):
    tree = ast.parse(path.read_text(encoding="utf-8"))
    docstrings = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Module | ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            first = node.body[0] if node.body else None
            if isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant):
                docstrings.add(id(first.value))
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant)
        and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


@pytest.mark.parametrize("name", ["writer.py", "rabatte.py", "__main__.py"])
def test_no_forbidden_sql_in_string_literals(name):
    for text in _string_literals(SEED_DIR / name):
        upper = text.upper()
        assert "DROP SCHEMA" not in upper
        assert "CASCADE" not in upper
        assert "TRUNCATE" not in upper
        assert "DELETE" not in upper


ALLOWED_CHARACTERS = re.compile(r"[\x20-\x7EäöüÄÖÜß\n]*")


def test_fixed_messages_use_only_german_and_ascii_characters(writer, seed_main):
    failure = writer._Failure("UniqueViolation", "23505", "customers_domain_key")
    messages = [
        writer.AUTOCOMMIT_MESSAGE,
        writer.NO_SCHEMA_MESSAGE,
        writer.DEPENDENT_MESSAGE,
        writer._describe_failure("Schreiben in die Datenbank fehlgeschlagen", failure),
        writer._describe_failure("Lesen der vorhandenen Tabellen fehlgeschlagen", failure),
        seed_main.USAGE_MESSAGE,
        seed_main.CONNECT_MESSAGE,
        GENERATED_LINE,
    ]
    for message in messages:
        assert ALLOWED_CHARACTERS.fullmatch(message), message
        message.encode("cp1252")


# Tests mit Datenbank


def _expected(real_data):
    return {
        "products": len(real_data.products),
        "customers": len(real_data.customers),
        "contacts": len(real_data.contacts),
        "discount_rules": len(real_data.discount_rules),
        "product_fits": len(real_data.product_fits),
        "activities": len(real_data.activities),
    }


def _counts(conn):
    return {
        name: conn.execute(f"SELECT count(*) FROM {name}").fetchone()[0] for name in RESET_TABLES
    }


def _existing_tables(conn):
    return [
        name
        for name in RESET_TABLES
        if conn.execute("SELECT to_regclass(%s) IS NOT NULL", (name,)).fetchone()[0]
    ]


def _fingerprint(conn):
    """Inhalt aller Tabellen über natürliche Schlüssel (ohne IDs), zum Vergleich zweier Läufe."""
    queries = {
        "products": "SELECT article_number, name, list_price, technical_data FROM products "
        "ORDER BY article_number",
        "customers": "SELECT domain, company_name, status, created_at FROM customers ORDER BY domain",
        "contacts": "SELECT c.domain, ct.email, ct.language, ct.created_at FROM contacts ct "
        "JOIN customers c ON c.id = ct.customer_id ORDER BY ct.email",
        "rules": "SELECT customer_status, product_category, min_quantity, max_discount_percent "
        "FROM discount_rules ORDER BY 1 NULLS FIRST, 2 NULLS FIRST, 3",
        "fits": "SELECT p.article_number, f.article_number FROM product_fits pf "
        "JOIN products p ON p.id = pf.product_id JOIN products f ON f.id = pf.fits_product_id "
        "ORDER BY 1, 2",
        "activities": "SELECT c.domain, ct.email, p.article_number, a.type, a.occurred_at, "
        "a.subject, a.amount_eur, a.created_by FROM activities a "
        "JOIN customers c ON c.id = a.customer_id "
        "LEFT JOIN contacts ct ON ct.id = a.contact_id "
        "LEFT JOIN products p ON p.id = a.product_id ORDER BY a.occurred_at, c.domain, a.subject",
    }
    return {name: conn.execute(query).fetchall() for name, query in queries.items()}


def _add_marker(conn):
    conn.execute(
        "INSERT INTO activities (customer_id, type, occurred_at, subject, summary, created_by) "
        "SELECT id, 'note', now(), 'Marker', 'Marker', 'agent' FROM customers ORDER BY id LIMIT 1"
    )


def _marker_count(conn):
    return conn.execute("SELECT count(*) FROM activities WHERE created_by = 'agent'").fetchone()[0]


def _run(writer, conn, target, richtlinien, confirm=None, data_dir=DATA_DIR):
    lines: list[str] = []
    counts = writer.run(conn, target, data_dir, SCHEMA_SQL, richtlinien, confirm, lines.append)
    return counts, "\n".join(lines)


def test_fresh_database_is_created_and_filled(writer, seed_conn, seed_target, real_data, tmp_path):
    counts, output = _run(writer, seed_conn, seed_target, tmp_path)
    assert counts == _expected(real_data)
    assert _counts(seed_conn) == _expected(real_data)
    assert "Keine vorhandenen Tabellen" in output
    assert (tmp_path / "rabatte.md").read_text(encoding="utf-8") == render_rabatte(real_data)


def test_second_run_needs_the_confirmation(writer, seed_conn, seed_target, real_data, tmp_path):
    _run(writer, seed_conn, seed_target, tmp_path)
    first = _fingerprint(seed_conn)
    _add_marker(seed_conn)
    expected = _expected(real_data)

    with pytest.raises(SeedGuardError):
        _run(writer, seed_conn, seed_target, tmp_path)
    assert _marker_count(seed_conn) == 1
    assert _counts(seed_conn) == {**expected, "activities": expected["activities"] + 1}

    _run(writer, seed_conn, seed_target, tmp_path, confirm=seed_target.host)
    assert _marker_count(seed_conn) == 0
    assert _counts(seed_conn) == expected
    assert _fingerprint(seed_conn) == first

    _run(writer, seed_conn, seed_target, tmp_path, confirm=seed_target.host.upper())
    assert _fingerprint(seed_conn) == first


def test_missing_confirmation_is_a_guard_error_not_a_write_error(
    writer, seed_conn, seed_target, tmp_path
):
    _run(writer, seed_conn, seed_target, tmp_path)
    with pytest.raises(SeedGuardError) as excinfo:
        _run(writer, seed_conn, seed_target, tmp_path, confirm="falscher.host")
    assert not isinstance(excinfo.value, writer.SeedWriteError)


def test_only_the_six_tables_are_dropped(writer, seed_conn, seed_target, seed_schema, tmp_path):
    seed_conn.execute("CREATE TABLE fremde_tabelle (id int)")
    seed_conn.execute("INSERT INTO fremde_tabelle VALUES (1), (2)")
    _run(writer, seed_conn, seed_target, tmp_path)
    _run(writer, seed_conn, seed_target, tmp_path, confirm=seed_target.host)
    assert seed_conn.execute("SELECT count(*) FROM fremde_tabelle").fetchone()[0] == 2
    schemas = seed_conn.execute(
        "SELECT count(*) FROM information_schema.schemata WHERE schema_name = %s", (seed_schema,)
    ).fetchone()[0]
    assert schemas == 1


def test_dependent_view_prevents_the_drop_because_there_is_no_cascade(
    writer, seed_conn, seed_target, real_data, tmp_path
):
    _run(writer, seed_conn, seed_target, tmp_path)
    seed_conn.execute("CREATE VIEW seed_view AS SELECT article_number FROM products")
    before = _fingerprint(seed_conn)
    with pytest.raises(writer.SeedWriteError) as excinfo:
        _run(writer, seed_conn, seed_target, tmp_path, confirm=seed_target.host)
    assert (
        str(excinfo.value) == "Abhängige Objekte verhindern das Löschen. Es wurde nichts verändert."
    )
    assert seed_conn.execute("SELECT to_regclass('seed_view') IS NOT NULL").fetchone()[0]
    assert _counts(seed_conn) == _expected(real_data)
    assert _fingerprint(seed_conn) == before


def test_failure_in_the_middle_restores_the_previous_state(
    writer, seed_conn, seed_target, real_data, tmp_path, monkeypatch
):
    _run(writer, seed_conn, seed_target, tmp_path)
    _add_marker(seed_conn)
    before = _fingerprint(seed_conn)

    def failing(*args, **kwargs):
        raise RuntimeError("Absichtlicher Fehler")

    monkeypatch.setattr(writer, "_insert_activities", failing)
    with pytest.raises(writer.SeedWriteError) as excinfo:
        _run(writer, seed_conn, seed_target, tmp_path, confirm=seed_target.host)
    monkeypatch.undo()
    assert "RuntimeError" in str(excinfo.value)
    assert "Absichtlicher" not in str(excinfo.value)
    assert _marker_count(seed_conn) == 1
    assert _fingerprint(seed_conn) == before
    assert _counts(seed_conn)["products"] == len(real_data.products)


def test_failure_on_a_fresh_database_leaves_no_tables(
    writer, seed_conn, seed_target, tmp_path, monkeypatch
):
    def failing(*args, **kwargs):
        raise RuntimeError("Absichtlicher Fehler")

    monkeypatch.setattr(writer, "_insert_product_fits", failing)
    with pytest.raises(writer.SeedWriteError):
        _run(writer, seed_conn, seed_target, tmp_path)
    monkeypatch.undo()
    assert _existing_tables(seed_conn) == []
    assert not (tmp_path / "rabatte.md").exists()


@pytest.mark.parametrize("case", ["kaputt", "pruefung"])
def test_data_errors_prevent_any_database_change(
    writer, seed_conn, seed_target, tmp_path, raw, case
):
    richtlinien = tmp_path / "richtlinien"
    richtlinien.mkdir()
    _run(writer, seed_conn, seed_target, richtlinien)
    _add_marker(seed_conn)
    before = _fingerprint(seed_conn)
    rabatte_before = (richtlinien / "rabatte.md").read_text(encoding="utf-8")
    if case == "kaputt":
        del raw["products"][0]["name"]
    else:
        raw["activities"][0]["article_number"] = "FB-9999"
    broken = tmp_path / "kaputte-daten"
    broken.mkdir()
    for name, rows in raw.items():
        (broken / f"{name}.json").write_text(json.dumps(rows), encoding="utf-8")
    with pytest.raises(SeedDataError):
        _run(writer, seed_conn, seed_target, richtlinien, confirm=seed_target.host, data_dir=broken)
    assert _marker_count(seed_conn) == 1
    assert _fingerprint(seed_conn) == before
    assert (richtlinien / "rabatte.md").read_text(encoding="utf-8") == rabatte_before


def test_wrong_host_changes_nothing(writer, seed_conn, seed_target, tmp_path):
    _run(writer, seed_conn, seed_target, tmp_path)
    _add_marker(seed_conn)
    before = _fingerprint(seed_conn)
    other = Target(host="anderer.example", dbname=seed_target.dbname)
    with pytest.raises(SeedGuardError):
        _run(writer, seed_conn, other, tmp_path, confirm="anderer.example")
    assert _marker_count(seed_conn) == 1
    assert _fingerprint(seed_conn) == before


def test_values_are_stored_exactly(writer, seed_conn, seed_target, real_data, tmp_path):
    _run(writer, seed_conn, seed_target, tmp_path)
    kind, power = seed_conn.execute(
        "SELECT jsonb_typeof(technical_data -> 'motor_power_kw'), "
        "(technical_data ->> 'motor_power_kw')::numeric "
        "FROM products WHERE article_number = 'FB-1001'"
    ).fetchone()
    assert kind == "number"
    assert power == Decimal("0.75")
    kind = seed_conn.execute(
        "SELECT jsonb_typeof(technical_data -> 'frame_material') FROM products "
        "WHERE article_number = 'FB-1008'"
    ).fetchone()[0]
    assert kind == "string"
    price = seed_conn.execute(
        "SELECT list_price FROM products WHERE article_number = 'ET-3006'"
    ).fetchone()[0]
    assert price == Decimal("38.50")
    amount = seed_conn.execute(
        "SELECT a.amount_eur FROM activities a JOIN products p ON p.id = a.product_id "
        "WHERE p.article_number = 'ET-3006' AND a.type = 'order'"
    ).fetchone()[0]
    assert amount == Decimal("1540.00")
    created = seed_conn.execute(
        "SELECT created_at FROM customers WHERE domain = 'brenner-automotive.example'"
    ).fetchone()[0]
    assert created.tzinfo is not None
    assert created == datetime.fromisoformat("2019-03-12T09:15:00+01:00")


def test_natural_keys_are_resolved_to_the_right_rows(
    writer, seed_conn, seed_target, real_data, tmp_path
):
    _run(writer, seed_conn, seed_target, tmp_path)
    fits = set(
        seed_conn.execute(
            "SELECT p.article_number, f.article_number FROM product_fits pf "
            "JOIN products p ON p.id = pf.product_id JOIN products f ON f.id = pf.fits_product_id"
        ).fetchall()
    )
    assert fits == {(fit.part, fit.fits) for fit in real_data.product_fits}
    notes = dict(
        seed_conn.execute(
            "SELECT p.article_number || '>' || f.article_number, pf.note FROM product_fits pf "
            "JOIN products p ON p.id = pf.product_id JOIN products f ON f.id = pf.fits_product_id"
        ).fetchall()
    )
    assert notes == {f"{fit.part}>{fit.fits}": fit.note for fit in real_data.product_fits}
    activities = Counter(
        seed_conn.execute(
            "SELECT c.domain, ct.email, p.article_number, a.type, a.occurred_at, a.amount_eur, "
            "a.created_by FROM activities a JOIN customers c ON c.id = a.customer_id "
            "LEFT JOIN contacts ct ON ct.id = a.contact_id "
            "LEFT JOIN products p ON p.id = a.product_id"
        ).fetchall()
    )
    expected = Counter(
        (
            a.customer_domain,
            a.contact_email,
            a.article_number,
            a.type,
            a.occurred_at,
            a.amount_eur,
            a.created_by,
        )
        for a in real_data.activities
    )
    assert activities == expected
    contacts = set(
        seed_conn.execute(
            "SELECT ct.email, c.domain FROM contacts ct JOIN customers c ON c.id = ct.customer_id"
        ).fetchall()
    )
    assert contacts == {(c.email, c.customer_domain) for c in real_data.contacts}


def test_output_before_deleting_names_the_target_and_never_a_secret(
    writer, seed_conn, seed_target, real_data, seed_schema, tmp_path
):
    _run(writer, seed_conn, seed_target, tmp_path)
    host = f"[{seed_target.host}]" if ":" in seed_target.host else seed_target.host
    url = f"postgresql://benutzer_x:Geheim%23123@{host}/{quote(seed_target.dbname)}"
    target = parse_target(url, {})
    lines: list[str] = []
    with pytest.raises(SeedGuardError) as excinfo:
        writer.run(seed_conn, target, DATA_DIR, SCHEMA_SQL, tmp_path, None, lines.append)
    text = "\n".join(lines)
    assert target.host in text
    assert target.dbname in text
    assert seed_schema in text
    for name, count in _expected(real_data).items():
        assert f"{name}: {count} Zeilen" in text
    assert "später vom Agenten und von Mitarbeitenden" in text
    for secret in ("Geheim", "benutzer_x", url):
        assert secret not in text
        assert secret not in str(excinfo.value)


def test_database_errors_never_contain_library_text(
    writer, seed_conn, seed_target, tmp_path, monkeypatch
):
    def duplicate_domain(cur, schema, data):
        query = (
            "INSERT INTO customers (company_name, domain, industry, country, status) "
            "VALUES (%s, %s, 'other', 'de', 'lead')"
        )
        cur.execute(query, ("GEHEIMFIRMA-XYZ", "geheim-xyz.example"))
        cur.execute(query, ("GEHEIMFIRMA-XYZ2", "geheim-xyz.example"))

    monkeypatch.setattr(writer, "_insert_customers", duplicate_domain)
    with pytest.raises(writer.SeedWriteError) as excinfo:
        _run(writer, seed_conn, seed_target, tmp_path)
    monkeypatch.undo()
    exc = excinfo.value
    assert str(exc) == (
        "Schreiben in die Datenbank fehlgeschlagen (UniqueViolation, SQLSTATE 23505, "
        'Constraint "customers_domain_key"). Die Datenbank wurde nicht verändert.'
    )
    for text in (str(exc), repr(exc), "".join(traceback.format_exception(exc))):
        for forbidden in ("duplicate key", "geheim", "GEHEIM", "Key (", "already exists"):
            assert forbidden not in text
    assert exc.__context__ is None
    assert exc.__cause__ is None


def test_rabatte_md_is_written_only_after_a_successful_transaction(
    writer, seed_conn, seed_target, real_data, tmp_path, monkeypatch
):
    folder = tmp_path / "richtlinien"
    folder.mkdir()
    path = folder / "rabatte.md"

    # Erfolgreicher Lauf auf frischer Datenbank: Die Datei wird erzeugt
    _run(writer, seed_conn, seed_target, folder)
    assert path.read_text(encoding="utf-8") == render_rabatte(real_data)

    # Löschschutz bricht ab (keine Bestätigung): Die Datei bleibt, wie sie war
    path.write_text("alt\n", encoding="utf-8")
    with pytest.raises(SeedGuardError):
        _run(writer, seed_conn, seed_target, folder, confirm=None)
    assert path.read_text(encoding="utf-8") == "alt\n"

    # Fehler in der Transaktion (Rollback): die Datei bleibt, wie sie war
    def failing(*args, **kwargs):
        raise RuntimeError("x")

    monkeypatch.setattr(writer, "_insert_discount_rules", failing)
    with pytest.raises(writer.SeedWriteError):
        _run(writer, seed_conn, seed_target, folder, confirm=seed_target.host)
    monkeypatch.undo()
    assert path.read_text(encoding="utf-8") == "alt\n"

    # Erfolg: die Datei wird neu erzeugt
    _run(writer, seed_conn, seed_target, folder, confirm=seed_target.host)
    assert path.read_text(encoding="utf-8") == render_rabatte(real_data)


def test_failing_rabatte_file_keeps_the_database_filled(
    writer, seed_conn, seed_target, real_data, tmp_path
):
    missing_folder = tmp_path / "gibt-es-nicht"
    with pytest.raises(writer.SeedWriteError) as excinfo:
        _run(writer, seed_conn, seed_target, missing_folder)
    assert str(excinfo.value) == (
        "rabatte.md konnte nicht geschrieben werden (FileNotFoundError). "
        "Die Datenbank wurde befüllt."
    )
    assert _counts(seed_conn) == _expected(real_data)


def test_executed_sql_contains_only_the_six_drops_and_no_cascade(
    writer, recorded_conn, seed_schema, tmp_path
):
    from db.seed.guard import Target

    conn, statements = recorded_conn
    target = Target(host=conn.info.host.lower(), dbname=conn.info.dbname)
    schema_text = SCHEMA_SQL.read_text(encoding="utf-8")
    expected_drops = [f'DROP TABLE IF EXISTS "{seed_schema}"."{name}"' for name in RESET_TABLES]

    for confirm in (None, target.host):
        start = len(statements)
        _run(writer, conn, target, tmp_path, confirm=confirm)
        executed = statements[start:]
        assert executed.count(schema_text) == 1
        own = [text for text in executed if text != schema_text]
        drops = [text for text in own if re.search(r"\bDROP\b", text, re.IGNORECASE)]
        assert drops == expected_drops
        for text in own:
            assert not re.search(r"\b(CASCADE|TRUNCATE|DELETE)\b", text, re.IGNORECASE)
            assert "DROP SCHEMA" not in text.upper()
        # Das Skript aus schema.sql selbst löscht nichts (Kommentare ausgenommen)
        code = re.sub(r"--[^\n]*", "", schema_text)
        assert not re.search(r"\b(DROP|CASCADE|TRUNCATE)\b|\bDELETE\s+FROM\b", code, re.IGNORECASE)
        # Die Reihenfolge der Befehle: erst alle DROP, dann das Schema, dann Einfügen
        first_insert = next(i for i, text in enumerate(executed) if text.startswith("INSERT"))
        assert executed.index(schema_text) < first_insert
        assert max(executed.index(text) for text in expected_drops) < executed.index(schema_text)
