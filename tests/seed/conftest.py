"""Fixtures für die Tests des Seed-Moduls (F05).

Die Tests laden die echten Stammdaten aus data/stammdaten/ oder eine veränderte Kopie davon
in einem temporären Ordner. Die meisten brauchen weder eine Datenbank noch Umgebungsvariablen.
Der Repo-Stamm kommt in sys.path, damit "import db.seed" auch mit einem blanken
"pytest tests/seed" funktioniert (bei "python -m pytest" ist er ohnehin enthalten).

Die Fixtures mit Datenbank (seed_*) laufen nur mit TEST_DATABASE_URL, sonst werden die Tests
übersprungen (in GitHub Actions ist das ein Fehler, wie in tests/db). Die URL-Prüfung
check_test_database_url wird aus tests/db/conftest.py per Dateipfad geladen, nicht kopiert.
Jeder Test bekommt ein leeres Schema test_<zufall> und eine äußere Transaktion mit Rollback.
"""

import copy
import importlib.util
import json
import os
import sys
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from db.seed.loader import SeedData

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "data" / "stammdaten"
FILE_NAMES = (
    "products",
    "product_fits",
    "customers",
    "contacts",
    "activities",
    "discount_rules",
)


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return DATA_DIR


@pytest.fixture(scope="session")
def real_data() -> "SeedData":
    """Die echten Stammdaten, einmal je Lauf geladen (die Daten sind unveränderlich)."""
    from db.seed.loader import load_seed_data

    return load_seed_data(DATA_DIR)


@pytest.fixture(scope="session")
def _original_raw() -> dict[str, list[Any]]:
    return {
        name: json.loads((DATA_DIR / f"{name}.json").read_text(encoding="utf-8"))
        for name in FILE_NAMES
    }


@pytest.fixture
def raw(_original_raw: dict[str, list[Any]]) -> dict[str, list[Any]]:
    """Tiefe Kopie der sechs JSON-Listen, die ein Test gezielt verändern darf."""
    return copy.deepcopy(_original_raw)


@pytest.fixture
def write_raw(tmp_path: Path) -> Callable[[dict[str, Any]], Path]:
    """Schreibt die (veränderten) Listen als JSON-Dateien nach tmp_path und gibt den Ordner zurück."""

    def _write(raw_data: dict[str, Any]) -> Path:
        for name, rows in raw_data.items():
            text = json.dumps(rows, ensure_ascii=False, indent=2)
            (tmp_path / f"{name}.json").write_text(text, encoding="utf-8")
        return tmp_path

    return _write


@pytest.fixture
def load_raw(write_raw: Callable[[dict[str, Any]], Path]) -> Callable[[dict[str, Any]], "SeedData"]:
    """Schreibt die veränderten Listen nach tmp_path und lädt sie mit dem Loader."""
    from db.seed.loader import load_seed_data

    def _load(raw_data: dict[str, Any]) -> "SeedData":
        return load_seed_data(write_raw(raw_data))

    return _load


# Fixtures mit Datenbank (nur mit TEST_DATABASE_URL)

DB_CONFTEST = ROOT / "tests" / "db" / "conftest.py"


class ExplodingConnection:
    """Stub, der bei jedem Zugriff scheitert: Beleg dafür, dass nichts auf die Datenbank zugreift."""

    def __getattr__(self, name: str) -> Any:
        raise AssertionError(f"Unerwarteter Zugriff auf die Verbindung: {name}")


@pytest.fixture
def exploding_conn() -> ExplodingConnection:
    return ExplodingConnection()


@pytest.fixture(scope="session")
def seed_support() -> Any:
    """Die Hilfen aus tests/db/conftest.py (URL-Prüfung), per Dateipfad geladen, nicht kopiert."""
    pytest.importorskip("psycopg")
    spec = importlib.util.spec_from_file_location("seed_tests_db_support", DB_CONFTEST)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def seed_db_url(seed_support: Any) -> str:
    """TEST_DATABASE_URL, geprüft mit check_test_database_url (nur lokaler Host, Port 5432)."""
    name = seed_support.ENV_NAME
    url = os.environ.get(name)
    if not url:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.fail(
                f"{name} fehlt in GitHub Actions: Die Seed-Tests dürfen dort nicht übersprungen werden."
            )
        pytest.skip(f"{name} ist nicht gesetzt: Tests mit Datenbank werden übersprungen.")
    try:
        seed_support.check_test_database_url(url)
    except ValueError as exc:
        pytest.fail(str(exc), pytrace=False)
    return url


@pytest.fixture(scope="session")
def seed_schema(seed_db_url: str) -> Any:
    """Ein leeres Schema test_<zufall> (ohne schema.sql), am Ende gelöscht, nie public."""
    import psycopg
    from psycopg import sql

    name = f"test_{uuid.uuid4().hex[:12]}"
    assert name != "public" and name.startswith("test_")
    with psycopg.connect(seed_db_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(sql.Identifier(name)))
        try:
            yield name
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(sql.Identifier(name)))


@pytest.fixture
def seed_conn(seed_db_url: str, seed_schema: str) -> Any:
    """Verbindung mit search_path auf das leere Testschema in einer äußeren Transaktion.

    Alles, auch das Neuanlegen der Tabellen, wird am Ende des Tests zurückgerollt. Die Funktion
    run() steckt darin in einem Savepoint.
    """
    import psycopg

    with (
        psycopg.connect(seed_db_url, options=f"-csearch_path={seed_schema}") as conn,
        conn.transaction(force_rollback=True),
    ):
        yield conn


@pytest.fixture
def seed_target(seed_conn: Any) -> Any:
    from db.seed.guard import Target

    return Target(host=seed_conn.info.host.lower(), dbname=seed_conn.info.dbname)


@pytest.fixture
def recorded_conn(seed_db_url: str, seed_schema: str) -> Any:
    """Wie seed_conn, aber jeder ausgeführte SQL-Text wird in statements mitgeschnitten.

    Gibt (Verbindung, statements) zurück. Texte aus sql-Objekten werden über as_string gerendert.
    """
    import psycopg
    from psycopg import sql

    statements: list[str] = []

    def render(query: Any, cursor: Any) -> str:
        if isinstance(query, sql.Composable):
            return query.as_string(cursor)
        return str(query)

    class RecordingCursor(psycopg.Cursor):
        def execute(self, query: Any, *args: Any, **kwargs: Any) -> Any:
            statements.append(render(query, self))
            return super().execute(query, *args, **kwargs)

        def executemany(self, query: Any, *args: Any, **kwargs: Any) -> Any:
            statements.append(render(query, self))
            return super().executemany(query, *args, **kwargs)

    with (
        psycopg.connect(
            seed_db_url, options=f"-csearch_path={seed_schema}", cursor_factory=RecordingCursor
        ) as conn,
        conn.transaction(force_rollback=True),
    ):
        yield conn, statements
