"""Fixtures für die Tests des Datenservice hoffmann-data (F06, F07).

Die Tests der Schnittstellen, der Konfiguration und der Token-Prüfung brauchen weder Datenbank noch
Netzwerk: Die ASGI-App läuft im Speicher hinter dem Starlette-TestClient (kein Port wird geöffnet).
Die Fixtures mit Datenbank (F07, unten) laufen nur mit TEST_DATABASE_URL (nur localhost:5432), sonst
werden die Tests übersprungen, in GitHub Actions ist das ein Fehler. Der Ordner heißt tests/mcp_server
und nicht tests/mcp, weil ein Ordner "mcp" das SDK-Paket überdecken könnte.

Der Code unter mcp-server/hoffmann_data wird erst in den Fixtures und Tests importiert, nicht beim
Einlesen der Datei. So scheitert jeder Test einzeln mit "No module named 'hoffmann_data'", solange der
Code fehlt. Konstanten und Hilfen stehen in mcp_testkit.py (dort kommt auch mcp-server in sys.path).

Der TestClient nutzt base_url mit 127.0.0.1:8000. Der Standardwert "testserver" würde vom
Host-Schutz des SDKs mit 421 abgelehnt.
"""

import importlib.util
import itertools
import json
import os
import secrets
import uuid
from collections.abc import Callable, Iterator
from contextlib import ExitStack, contextmanager
from typing import Any

import psycopg
import pytest
from mcp_testkit import (
    BASE_URL,
    MCP_PATH,
    PRODUCTS_JSON,
    ROOT,
    RPC_HEADERS,
    parse_rpc_body,
    render_role_script,
)
from psycopg import sql
from psycopg.types.json import Jsonb
from starlette.testclient import TestClient


@pytest.fixture
def token() -> str:
    """Zufälliges Test-Token je Test: 43 Zeichen, nur URL-sichere Zeichen, keine Leerzeichen."""
    return secrets.token_urlsafe(32)


@pytest.fixture
def make_app() -> Callable[..., Any]:
    """Baut die ASGI-App aus einer Umgebung (dict), ohne die echte Umgebung zu lesen."""

    def _make(env: dict[str, str]) -> Any:
        from hoffmann_data.config import load_config
        from hoffmann_data.server import create_app

        return create_app(load_config(env))

    return _make


@pytest.fixture
def app(make_app: Callable[..., Any], token: str) -> Any:
    return make_app({"MCP_SERVER_TOKEN": token})


@pytest.fixture
def client(app: Any) -> Iterator[TestClient]:
    """TestClient mit laufendem Lifespan (der Session-Manager des SDKs startet darin)."""
    with TestClient(app, base_url=BASE_URL) as c:
        yield c


@pytest.fixture
def auth_headers(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


@pytest.fixture
def rpc(client: TestClient, auth_headers: dict[str, str]) -> Callable[..., dict[str, Any]]:
    """Schickt rohes JSON-RPC mit gültigem Token an /mcp und gibt die geparste Antwort zurück.

    Das geht ohne initialize, weil der Server zustandslos läuft (stateless_http=True, ADR 0003).
    Das ist die Stelle, die Auth und Protokoll gemeinsam prüft; mcp.Client in-process würde die
    Auth umgehen.
    """
    counter = iter(range(1, 10_000))

    def _call(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {"jsonrpc": "2.0", "id": next(counter), "method": method, "params": params or {}}
        response = client.post(MCP_PATH, json=payload, headers={**RPC_HEADERS, **auth_headers})
        assert response.status_code == 200, response.text
        return parse_rpc_body(response)

    return _call


# Fixtures mit Datenbank (F07). Sie laufen nur mit TEST_DATABASE_URL; die Regeln entsprechen tests/db:
# nur localhost:5432, DATABASE_URL wird nie benutzt, ein eigenes Schema test_<zufall> je Sitzung (nie
# public), eine äußere Transaktion je Test mit Rollback. Fehlt die Variable, werden die Tests lokal
# übersprungen; in GitHub Actions ist das ein Fehler, damit die Pipeline nie grün ist, ohne dass sie lief.
# Die Tests ohne Datenbank (F06) brauchen diese Fixtures nicht und laufen weiter überall.

DB_CONFTEST = ROOT / "tests" / "db" / "conftest.py"
SCHEMA_SQL = ROOT / "db" / "schema.sql"
CONNECT_TIMEOUT_SECONDS = 5
UNREACHABLE_MESSAGE = (
    "Test-Datenbank unter localhost:5432 nicht erreichbar. "
    "TEST_DATABASE_URL prüfen und Postgres starten."
)


def _connect(url: str, **kwargs: Any) -> psycopg.Connection[Any]:
    """Verbindet mit Zeitlimit. Ist die Datenbank nicht erreichbar, bricht der Test mit fester Meldung ab.

    Das ist ein Fehler, kein Skip: TEST_DATABASE_URL ist gesetzt. Die Meldung enthält weder URL noch
    Benutzer noch Passwort noch den Text der Ausnahme; geworfen wird außerhalb des except-Blocks.
    """
    unreachable = False
    try:
        return psycopg.connect(url, connect_timeout=CONNECT_TIMEOUT_SECONDS, **kwargs)
    except psycopg.OperationalError:
        unreachable = True
    assert unreachable
    pytest.fail(UNREACHABLE_MESSAGE, pytrace=False)


@pytest.fixture(scope="session")
def db_support() -> Any:
    """Die Hilfen aus tests/db/conftest.py (URL-Prüfung), per Dateipfad geladen, nicht kopiert."""
    spec = importlib.util.spec_from_file_location("mcp_tests_db_support", DB_CONFTEST)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="session")
def db_url(db_support: Any) -> str:
    """TEST_DATABASE_URL, geprüft mit check_test_database_url (nur lokaler Host, Port 5432)."""
    name = db_support.ENV_NAME
    url = os.environ.get(name)
    if not url:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.fail(
                f"{name} fehlt in GitHub Actions: Die MCP-Tests mit Datenbank dürfen dort nicht "
                "übersprungen werden.",
                pytrace=False,
            )
        pytest.skip(f"{name} ist nicht gesetzt: Tests mit Datenbank werden übersprungen.")
    try:
        db_support.check_test_database_url(url)
    except ValueError as exc:
        pytest.fail(str(exc), pytrace=False)
    return url


@pytest.fixture(scope="session")
def db_schema(db_url: str) -> Iterator[str]:
    """Legt test_<zufall> an, spielt db/schema.sql ein und löscht das Schema am Ende."""
    name = f"test_{uuid.uuid4().hex[:12]}"
    assert name != "public" and name.startswith("test_")
    ident = sql.Identifier(name)
    with _connect(db_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(ident))
        try:
            # search_path nur auf das Testschema: unqualifizierte Namen landen nie in public
            with _connect(db_url, autocommit=True, options=f"-csearch_path={name}") as setup:
                setup.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
            yield name
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(ident))


@pytest.fixture
def conn(db_url: str, db_schema: str) -> Iterator[psycopg.Connection[Any]]:
    """Eine äußere Transaktion je Test, immer zurückgerollt (auch Rollen, Policies und Testdaten)."""
    with (
        _connect(db_url, options=f"-csearch_path={db_schema}") as c,
        c.transaction(force_rollback=True),
    ):
        yield c


@pytest.fixture
def ro_role(conn: psycopg.Connection[Any]) -> str:
    """Spielt das Rollen-Skript unter einem Zufallsnamen ein (in der Test-Transaktion) und gibt ihn zurück.

    Braucht lokal das Recht CREATEROLE (wie der RLS-Test in tests/db); in der CI ist postgres Superuser.
    """
    name = f"ro_test_{uuid.uuid4().hex[:12]}"
    conn.execute(render_role_script(name))
    return name


_counter = itertools.count(1)


class Make:
    """Fabrik für gültige Minimalzeilen; Overrides per Schlüsselwort. Gibt die id zurück."""

    def __init__(self, conn: psycopg.Connection[Any]) -> None:
        self._conn = conn

    def _insert(self, table: str, defaults: dict[str, Any], overrides: dict[str, Any]) -> int:
        row = {**defaults, **overrides}
        cols = sql.SQL(", ").join(sql.Identifier(k) for k in row)
        marks = sql.SQL(", ").join(sql.Placeholder() for _ in row)
        query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING id").format(
            sql.Identifier(table), cols, marks
        )
        values = [
            Jsonb(v) if k == "technical_data" and v is not None else v for k, v in row.items()
        ]
        found = self._conn.execute(query, values).fetchone()
        assert found is not None
        return int(found[0])

    def product(self, **kw: Any) -> int:
        n = next(_counter)
        defaults = {
            "article_number": f"AB-{n:04d}",
            "name": "Testartikel",
            "category": "conveyor",
            "description": "Testbeschreibung",
            "list_price": 10,
            "price_unit": "piece",
            "lead_time_days": 5,
        }
        return self._insert("products", defaults, kw)

    def customer(self, **kw: Any) -> int:
        n = next(_counter)
        defaults = {
            "company_name": f"Testfirma {n}",
            "domain": f"firma{n}.example",
            "industry": "other",
            "country": "de",
            "status": "existing",
        }
        return self._insert("customers", defaults, kw)

    def contact(self, customer_id: int, **kw: Any) -> int:
        n = next(_counter)
        defaults = {
            "customer_id": customer_id,
            "first_name": "Erika",
            "last_name": "Muster",
            "email": f"kontakt{n}@firma.example",
            "language": "de",
        }
        return self._insert("contacts", defaults, kw)

    def activity(self, customer_id: int, **kw: Any) -> int:
        defaults = {
            "customer_id": customer_id,
            "type": "inquiry",
            "occurred_at": "2026-01-01T10:00:00+00",
            "subject": "Betreff",
            "summary": "Zusammenfassung",
            "created_by": "seed",
        }
        return self._insert("activities", defaults, kw)

    def fit(self, product_id: int, fits_product_id: int, **kw: Any) -> int:
        defaults = {"product_id": product_id, "fits_product_id": fits_product_id}
        return self._insert("product_fits", defaults, kw)

    def rule(self, **kw: Any) -> int:
        defaults = {"max_discount_percent": 5, "description": "Testregel"}
        return self._insert("discount_rules", defaults, kw)


@pytest.fixture
def make(conn: psycopg.Connection[Any]) -> Make:
    return Make(conn)


# Fixtures für die Werkzeug-Tests (F07). Die Werkzeuge laufen durch die ganze App (Token, Transport, SDK).
# Als Datenbank dient die Test-Verbindung unter der lesenden Rolle: Die Werkzeuge sehen genau die Rechte
# und Policies der echten Rolle und die nicht committeten Testdaten der äußeren Transaktion.

ToolCall = Callable[..., dict[str, Any]]


class RoDatabase:
    """Test-Double für hoffmann_data.db.Database: gibt die Test-Verbindung unter der lesenden Rolle heraus.

    Jeder Aufruf läuft in einem Savepoint mit SET LOCAL ROLE. RESET ROLE steht nur am normalen Ende: Bei
    einer Ausnahme rollt der Savepoint zurück und nimmt SET LOCAL mit. Die Werkzeuge müssen die Zeilenform
    selbst setzen (kein row_factory der Verbindung voraussetzen).
    """

    def __init__(self, conn: psycopg.Connection[Any], role: str) -> None:
        self._conn = conn
        self._role = role

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection[Any]]:
        with self._conn.transaction():
            self._conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(self._role)))
            yield self._conn
            self._conn.execute("RESET ROLE")


def _tool_caller(client: TestClient, headers: dict[str, str]) -> ToolCall:
    """Aufrufer für tools/call mit Token: gibt `result` zurück (auch bei isError), Protokollfehler brechen ab."""
    counter = iter(range(1, 10_000))

    def _call(name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
        payload = {
            "jsonrpc": "2.0",
            "id": next(counter),
            "method": "tools/call",
            "params": {"name": name, "arguments": {} if arguments is None else arguments},
        }
        response = client.post(MCP_PATH, json=payload, headers={**RPC_HEADERS, **headers})
        assert response.status_code == 200, response.text
        body = parse_rpc_body(response)
        assert "error" not in body, body
        return body["result"]

    return _call


@pytest.fixture
def tool_call_for(token: str) -> Iterator[Callable[[Any], ToolCall]]:
    """Fabrik: baut die App mit der übergebenen Datenbank (auch None) und gibt den Aufrufer zurück."""
    from hoffmann_data.config import load_config
    from hoffmann_data.server import create_app

    with ExitStack() as stack:

        def _for(database: Any) -> ToolCall:
            app = create_app(load_config({"MCP_SERVER_TOKEN": token}), database)
            client = stack.enter_context(TestClient(app, base_url=BASE_URL))
            return _tool_caller(client, {"Authorization": f"Bearer {token}"})

        yield _for


@pytest.fixture
def tool_app(conn: psycopg.Connection[Any], ro_role: str, token: str) -> Any:
    """Die App mit der Test-Verbindung unter der lesenden Rolle als Datenbank."""
    from hoffmann_data.config import load_config
    from hoffmann_data.server import create_app

    return create_app(load_config({"MCP_SERVER_TOKEN": token}), RoDatabase(conn, ro_role))


@pytest.fixture
def tool_call(tool_app: Any, token: str) -> Iterator[ToolCall]:
    """tools/call über die ganze App mit Token; gibt `result` zurück."""
    with TestClient(tool_app, base_url=BASE_URL) as client:
        yield _tool_caller(client, {"Authorization": f"Bearer {token}"})


@pytest.fixture
def raw_tool_call(tool_app: Any, token: str) -> Iterator[Callable[..., Any]]:
    """Roher tools/call: gibt die HTTP-Antwort zurück, ohne sie zu prüfen.

    Der Body entsteht mit json.dumps (ASCII, Sonderzeichen als \\uXXXX) und wird als Bytes gesendet. So
    lässt sich auch ein einzelnes Surrogat ("\\ud800") verschicken; der JSON-Encoder von httpx würde es schon
    im Test ablehnen. Für Fälle, in denen Transport oder SDK die Anfrage vor dem Werkzeug abweisen könnten.
    """
    with TestClient(tool_app, base_url=BASE_URL) as client:
        headers = {**RPC_HEADERS, "Authorization": f"Bearer {token}"}

        def _post(name: str, arguments: dict[str, Any]) -> Any:
            payload = {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": name, "arguments": arguments},
            }
            body = json.dumps(payload).encode("ascii")
            return client.post(MCP_PATH, content=body, headers=headers)

        yield _post


@pytest.fixture
def catalog(make: Make) -> dict[str, int]:
    """Lädt den ganzen Katalog (products.json, product_fits.json) in die Test-Transaktion.

    Gibt Artikelnummer -> interne id zurück, damit Tests Zuordnungen anlegen können.
    """
    products = json.loads(PRODUCTS_JSON.read_text(encoding="utf-8"))
    ids = {p["article_number"]: make._insert("products", {}, p) for p in products}
    fits = json.loads((PRODUCTS_JSON.parent / "product_fits.json").read_text(encoding="utf-8"))
    for fit in fits:
        make.fit(ids[fit["part"]], ids[fit["fits"]], note=fit["note"])
    return ids


class ConnectLog:
    """Protokoll der psycopg.connect-Aufrufe: Zahl der Versuche und die geöffneten Verbindungen."""

    def __init__(self) -> None:
        self.calls = 0
        self.opened: list[psycopg.Connection[Any]] = []


@pytest.fixture
def connect_log(monkeypatch: pytest.MonkeyPatch) -> ConnectLog:
    """Zählt jeden Aufruf von psycopg.connect und reicht ihn unverändert durch."""
    log = ConnectLog()
    real_connect = psycopg.connect

    def counting(*args: Any, **kwargs: Any) -> psycopg.Connection[Any]:
        log.calls += 1
        connection = real_connect(*args, **kwargs)
        log.opened.append(connection)
        return connection

    monkeypatch.setattr(psycopg, "connect", counting)
    return log


@pytest.fixture
def real_database(db_url: str, db_schema: str) -> Any:
    """Die echte Database auf dem Test-Server, mit search_path im Testschema (leere Tabellen)."""
    from hoffmann_data.db import Database

    return Database(db_url, options=f"-csearch_path={db_schema}")
