"""Gemeinsame Konstanten und Hilfen der Tests von hoffmann-data (F06).

Eigenes Modul statt "from conftest import ...": In tests/ liegen mehrere conftest.py (db, seed,
mcp_server), ein Import über den Namen "conftest" könnte die falsche Datei treffen.
"""

import json
import re
import sys
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Self

ROOT = Path(__file__).resolve().parents[2]
MCP_SERVER_DIR = ROOT / "mcp-server"
if str(MCP_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_SERVER_DIR))

# Rollen-Skript (F07). Die Tests führen es unter einem Zufallsnamen aus, damit die clusterweite Rolle
# data_service_ro nicht kollidiert. Ersetzt wird nur der Name, per exakter Textersetzung.
ROLE_SCRIPT = ROOT / "db" / "roles" / "data_service_ro.sql"
ROLE_NAME = "data_service_ro"
# So oft steht der Rollenname im Skript. Festgeschrieben: Ändert sich das Skript, ist die Zahl bewusst
# anzupassen (sonst könnte die Ersetzung im Test eine Stelle verpassen).
# Die 13 Vorkommen: 1 Abfrage (rolname = ...), 1 CREATE ROLE, 3 ALTER ROLE (Attribute, read_only,
# statement_timeout), 1 GRANT USAGE, 1 REVOKE, 1 GRANT SELECT, 5 CREATE POLICY (je Tabelle eine).
EXPECTED_ROLE_NAME_COUNT = 13
ROLE_NAME_PATTERN = re.compile(r"[a-z_][a-z0-9_]{0,62}")
# Tabellen, die die Rolle lesen darf (discount_rules bewusst nicht; F09 liest die Richtlinien aus Dateien, ADR 0006)
READABLE_TABLES = ("products", "product_fits", "customers", "contacts", "activities")


def render_role_script(name: str) -> str:
    """Das Rollen-Skript mit dem Rollennamen `name`. Bricht ab, wenn die Ersetzung nicht sauber greift."""
    assert ROLE_NAME_PATTERN.fullmatch(name), "ungültiger Rollenname"
    assert ROLE_NAME not in name or name == ROLE_NAME, (
        "Testname darf den Originalnamen nicht enthalten"
    )
    text = ROLE_SCRIPT.read_text(encoding="utf-8")
    assert text.count(ROLE_NAME) == EXPECTED_ROLE_NAME_COUNT, "Zahl der Vorkommen weicht ab"
    rendered = text.replace(ROLE_NAME, name)
    if name != ROLE_NAME:
        assert ROLE_NAME not in rendered, "Originalname nach der Ersetzung übrig"
        assert rendered.count(name) == EXPECTED_ROLE_NAME_COUNT
    return rendered


# Schreibrolle (F08, ADR 0005). Wie oben: Das Skript läuft in Tests unter einem Zufallsnamen. Die feste Zahl der
# Vorkommen kommt erst, wenn das Skript existiert; bis dahin gilt "mindestens ein Treffer".
WRITE_ROLE_SCRIPT = ROOT / "db" / "roles" / "data_service_rw.sql"
WRITE_ROLE_NAME = "data_service_rw"


def render_write_role_script(name: str) -> str:
    """Das Schreibrollen-Skript mit dem Rollennamen `name`. Bricht ab, wenn die Ersetzung nicht sauber greift."""
    assert ROLE_NAME_PATTERN.fullmatch(name), "ungültiger Rollenname"
    assert WRITE_ROLE_NAME not in name or name == WRITE_ROLE_NAME, (
        "Testname darf den Originalnamen nicht enthalten"
    )
    text = WRITE_ROLE_SCRIPT.read_text(encoding="utf-8")
    occurrences = text.count(WRITE_ROLE_NAME)
    assert occurrences > 0, "der Rollenname steht nicht im Skript"
    rendered = text.replace(WRITE_ROLE_NAME, name)
    if name != WRITE_ROLE_NAME:
        assert WRITE_ROLE_NAME not in rendered, "Originalname nach der Ersetzung übrig"
        assert rendered.count(name) == occurrences
    return rendered


@contextmanager
def as_role(conn: Any, role: str) -> Iterator[None]:
    """Führt den Block unter SET LOCAL ROLE aus und setzt die Rolle am Ende zurück."""
    from psycopg import sql

    conn.execute(sql.SQL("SET LOCAL ROLE {}").format(sql.Identifier(role)))
    try:
        yield
    finally:
        conn.execute("RESET ROLE")


BASE_URL = "http://127.0.0.1:8000"
MCP_PATH = "/mcp"

TOOL_NAMES = {"search_products", "get_product", "find_customer", "create_lead", "log_activity"}
# Drei Lese- und zwei Schreib-Werkzeuge, alle echt (F07, F08). Die Schreib-Werkzeuge: WRITE_TOOLS weiter unten.
# Seit F09 sind auch beide Resources und der Prompt echt; NOT_IMPLEMENTED dient nur noch als Gegenprobe ("kommt nie vor").
READ_TOOLS = {"search_products", "get_product", "find_customer"}
PRODUCTS_JSON = ROOT / "data" / "stammdaten" / "products.json"
RESOURCE_URIS = {"policy://tonalitaet", "policy://rabatte"}
PROMPT_NAMES = {"antwort_entwurf"}
NOT_IMPLEMENTED = "noch nicht implementiert"
INTERNAL_ERROR_CODE = -32603
MAX_REQUEST_BODY_BYTES = 256 * 1024

RPC_HEADERS = {
    "Content-Type": "application/json",
    "Accept": "application/json, text/event-stream",
}


def parse_rpc_body(response: Any) -> dict[str, Any]:
    """Liest die Antwort auf eine JSON-RPC-Anfrage: reines JSON oder ein Ereignisstrom (SSE)."""
    content_type = response.headers.get("content-type", "")
    if content_type.startswith("text/event-stream"):
        for line in response.text.splitlines():
            if line.startswith("data:"):
                return json.loads(line[len("data:") :].strip())
        raise AssertionError(f"Kein data:-Ereignis im Strom: {response.text!r}")
    return response.json()


# Marker für "Eingabewert darf nirgends zurückkommen" (Antwort, Log, stdout, stderr)
SECRET_MARKER = "GEHEIM-4711"


def error_text(result: dict[str, Any]) -> str:
    """Text eines Werkzeugfehlers (`result` von tools/call mit isError). Bricht ab, wenn es kein Fehler ist."""
    assert result.get("isError") is True, f"kein Fehler, sondern: {result!r}"
    return " ".join(c.get("text", "") for c in result["content"])


def structured(result: dict[str, Any]) -> dict[str, Any]:
    """`structuredContent` eines erfolgreichen Werkzeugaufrufs. Bricht mit dem Fehlertext ab, wenn isError."""
    assert result.get("isError") is not True, f"Werkzeugfehler: {error_text(result)}"
    content = result.get("structuredContent")
    assert isinstance(content, dict), f"kein structuredContent: {result!r}"
    return content


def all_keys(value: Any) -> set[str]:
    """Alle Schlüssel aller verschachtelten Objekte (Listen werden durchlaufen)."""
    if isinstance(value, dict):
        keys = {str(k) for k in value}
        for item in value.values():
            keys |= all_keys(item)
        return keys
    if isinstance(value, list):
        keys: set[str] = set()
        for item in value:
            keys |= all_keys(item)
        return keys
    return set()


def assert_no_internal_ids(value: Any) -> None:
    """Keine Ausgabe nennt interne IDs: kein Schlüssel `id` und keiner, der auf `_id` endet."""
    found = sorted(k for k in all_keys(value) if k == "id" or k.endswith("_id"))
    assert not found, f"interne IDs in der Ausgabe: {found}"


def check_rejected_before_database(
    response: Any, fixed_message: str, database_error: str, *, early_rejection_allowed: bool
) -> str:
    """Prüft die rohe HTTP-Antwort auf einen Aufruf mit unzulässigem Zeichen.

    Erwartet: Das Werkzeug läuft und lehnt mit seiner festen Meldung ab (`fixed_message`), ohne
    `database_error` und ohne structuredContent. Gibt dann "werkzeug" zurück.
    Hat schon Transport oder SDK die Anfrage abgewiesen (HTTP-Fehler oder JSON-RPC-Fehler), ist das nur mit
    `early_rejection_allowed` zulässig; dann gibt die Funktion "vor-werkzeug" zurück und druckt Status und
    Text der Abweisung (sichtbar mit pytest -s). In keinem Fall darf die Datenbank erreicht worden sein.
    """
    assert database_error not in response.text, "der Wert hat die Datenbank erreicht"
    body: dict[str, Any] = {}
    if response.status_code == 200:
        body = parse_rpc_body(response)
        if "result" in body:
            result = body["result"]
            assert fixed_message in error_text(result)
            assert not result.get("structuredContent")
            return "werkzeug"
    assert response.status_code >= 400 or "error" in body, f"nicht abgewiesen: {response.text!r}"
    assert early_rejection_allowed, (
        f"vor dem Werkzeug abgewiesen (HTTP {response.status_code}): {response.text[:300]!r}"
    )
    print(
        f"ABGEWIESEN VOR DEM WERKZEUG: HTTP {response.status_code}, Text: {response.text[:300]!r}"
    )
    return "vor-werkzeug"


# Datenbank-URLs für die Tests von dburl.py und für den Paritätstest gegen db/seed/guard.py (F07).
# Alle Zugangswerte enthalten "geheim", damit Tests prüfen können, dass keine Meldung sie nennt.
DB_SECRETS = ("geheimuser", "geheimpasswort", "geheimhost", "geheimdb")
_CRED = "geheimuser:geheimpasswort"

# (id, url): werden angenommen
DBURL_VALID: list[tuple[str, str]] = [
    ("localhost", f"postgresql://{_CRED}@localhost:5432/geheimdb"),
    ("schema-postgres", f"postgres://{_CRED}@127.0.0.1/geheimdb"),
    ("ipv6-loopback", f"postgresql://{_CRED}@[::1]:5432/geheimdb"),
    ("remote-require", f"postgresql://{_CRED}@geheimhost.example:5432/geheimdb?sslmode=require"),
    ("remote-verify-ca", f"postgresql://{_CRED}@geheimhost.example/geheimdb?sslmode=verify-ca"),
    ("remote-verify-full", f"postgresql://{_CRED}@geheimhost.example/geheimdb?sslmode=verify-full"),
    (
        "pooler-benutzer-mit-punkt",
        "postgresql://data_service_ro.geheimuser:geheimpasswort@geheimhost.example:6543/geheimdb"
        "?sslmode=require",
    ),
    ("localhost-gross", f"postgresql://{_CRED}@LOCALHOST:5432/geheimdb"),
    ("passwort-prozentkodiert", "postgresql://geheimuser:geheimpasswort%40x@localhost/geheimdb"),
    ("passwort-mit-komma", "postgresql://geheimuser:geheimpasswort,2@localhost/geheimdb"),
    (
        "remote-weitere-parameter",
        f"postgresql://{_CRED}@geheimhost.example/geheimdb?sslmode=require&connect_timeout=5",
    ),
    ("datenbankname-63-zeichen", f"postgresql://{_CRED}@localhost/" + "d" * 63),
]

# (id, url, umgebung): werden abgelehnt. Jeder Grund aus db/seed/guard.py kommt vor, damit der
# Paritätstest belegt, dass dburl nicht lockerer ist als das Seed-Skript.
DBURL_INVALID: list[tuple[str, str | None, dict[str, str]]] = [
    ("none", None, {}),
    ("leer", "", {}),
    ("nur-leerzeichen", "   ", {}),
    ("fremdes-schema", f"mysql://{_CRED}@localhost/geheimdb", {}),
    ("schema-grossgeschrieben", f"POSTGRESQL://{_CRED}@localhost/geheimdb", {}),
    ("kein-schema", f"{_CRED}@localhost/geheimdb", {}),
    ("leerzeichen-vorn", f" postgresql://{_CRED}@localhost/geheimdb", {}),
    ("leerzeichen-in-url", f"postgresql://{_CRED}@localhost/geheim db", {}),
    ("zeilenumbruch", f"postgresql://{_CRED}@localhost/geheimdb\n", {}),
    ("tabulator", f"postgresql://{_CRED}@localhost/geheimdb\t", {}),
    ("nul", f"postgresql://{_CRED}@localhost/geheimdb\x00", {}),
    ("nicht-ascii", "postgresql://geheimuser:geheimpasswörter@localhost/geheimdb", {}),
    ("einzelner-surrogat", f"postgresql://{_CRED}@localhost/\ud800", {}),
    ("raute-im-passwort", "postgresql://geheimuser:geheim#passwort@localhost/geheimdb", {}),
    ("zwei-at", "postgresql://geheimuser:geheim@passwort@localhost/geheimdb", {}),
    ("zwei-hosts", f"postgresql://{_CRED}@localhost,geheimhost.example/geheimdb", {}),
    ("kein-host-unix-socket", "postgresql:///geheimdb?user=geheimuser", {}),
    ("prozent-im-host", f"postgresql://{_CRED}@loc%61lhost/geheimdb", {}),
    ("port-keine-zahl", f"postgresql://{_CRED}@localhost:abc/geheimdb", {}),
    ("port-zu-gross", f"postgresql://{_CRED}@localhost:99999/geheimdb", {}),
    ("kein-datenbankname", f"postgresql://{_CRED}@localhost", {}),
    ("datenbankname-mit-leerzeichen", f"postgresql://{_CRED}@localhost/geheim%20db", {}),
    ("param-host", f"postgresql://{_CRED}@localhost/geheimdb?host=geheimhost.example", {}),
    ("param-hostaddr", f"postgresql://{_CRED}@localhost/geheimdb?hostaddr=10.0.0.1", {}),
    ("param-service", f"postgresql://{_CRED}@localhost/geheimdb?service=geheimdb", {}),
    ("param-dbname", f"postgresql://{_CRED}@localhost/geheimdb?dbname=geheimdb", {}),
    ("env-pghostaddr", f"postgresql://{_CRED}@localhost/geheimdb", {"PGHOSTADDR": "10.0.0.1"}),
    ("env-pgservice", f"postgresql://{_CRED}@localhost/geheimdb", {"PGSERVICE": "geheimdb"}),
    ("remote-ohne-sslmode", f"postgresql://{_CRED}@geheimhost.example/geheimdb", {}),
    (
        "remote-sslmode-disable",
        f"postgresql://{_CRED}@geheimhost.example/geheimdb?sslmode=disable",
        {},
    ),
    (
        "remote-sslmode-prefer",
        f"postgresql://{_CRED}@geheimhost.example/geheimdb?sslmode=prefer",
        {},
    ),
    ("remote-sslmode-allow", f"postgresql://{_CRED}@geheimhost.example/geheimdb?sslmode=allow", {}),
    ("remote-sslmode-leer", f"postgresql://{_CRED}@geheimhost.example/geheimdb?sslmode=", {}),
    (
        "remote-sslmode-gross",
        f"postgresql://{_CRED}@geheimhost.example/geheimdb?sslmode=REQUIRE",
        {},
    ),
    (
        "remote-sslmode-name-gross",
        f"postgresql://{_CRED}@geheimhost.example/geheimdb?SSLMODE=require",
        {},
    ),
    (
        "remote-sslmode-doppelt",
        f"postgresql://{_CRED}@geheimhost.example/geheimdb?sslmode=require&sslmode=require",
        {},
    ),
    ("ipv6-unvollstaendig", f"postgresql://{_CRED}@[::1/geheimdb", {}),
    (
        "fragezeichen-im-passwort",
        "postgresql://geheimuser:geheimpasswort?x@localhost/geheimdb",
        {},
    ),
    (
        "param-host-gross",
        f"postgresql://{_CRED}@localhost/geheimdb?HOST=geheimhost.example",
        {},
    ),
    ("datenbankname-zu-lang", f"postgresql://{_CRED}@localhost/" + "d" * 64, {}),
    ("datenbankname-ungueltiges-utf8", f"postgresql://{_CRED}@localhost/geheim%FF", {}),
    ("datenbankname-mit-slash", f"postgresql://{_CRED}@localhost/geheimdb/zweiter", {}),
]


# Schreibweg (F08 Etappe 2, ADR 0005 Punkte 2 und 8). Die Zugangswerte der Schreib-URL enthalten "geheim",
# damit Tests prüfen können, dass keine Meldung sie nennt.
WRITE_URL_NAME = "MCP_SERVER_WRITE_DATABASE_URL"
WRITE_TOKEN_NAME = "MCP_SERVER_WRITE_TOKEN"
GOOD_READ_URL = f"postgresql://{_CRED}@localhost:5432/geheimdb"
GOOD_WRITE_URL = "postgresql://geheimschreiber:geheimschreibpasswort@localhost:5432/geheimdb"
WRITE_SECRETS = ("geheimschreiber", "geheimschreibpasswort")

# Zeilen, die Tests mit echtem Commit anlegen, tragen diesen Marker in der Domain (".example" ist Pflicht des
# Schemas). Aufgeräumt wird nur, was den Marker trägt; ohne Marker löscht nichts.
ROW_MARKER = "mcp-schreibtest"


def marker_row() -> dict[str, str]:
    """Eindeutige Testwerte für einen Kunden und einen Kontakt, alle mit dem Marker."""
    import uuid

    suffix = uuid.uuid4().hex[:12]
    return {
        "company_name": f"{ROW_MARKER} {suffix}",
        "domain": f"{ROW_MARKER}-{suffix}.example",
        "email": f"kontakt@{ROW_MARKER}-{suffix}.example",
    }


def delete_marker_rows(conn: Any, pattern: str = f"{ROW_MARKER}-%") -> None:
    """Löscht die Marker-Kunden samt Aktivitäten und Kontakten (in dieser Reihenfolge, wegen der Fremdschlüssel).

    `pattern` ist ein LIKE-Muster für die Domain und muss mit "<Marker>-" beginnen; sonst ValueError und es
    wird nichts gelöscht. Die Verbindung muss ein Commit selbst erledigen (autocommit) oder der Aufrufer committet.
    """
    if not pattern.startswith(f"{ROW_MARKER}-"):
        raise ValueError("Das Muster muss mit dem Marker beginnen.")
    owned = "(SELECT id FROM customers WHERE domain LIKE %s)"
    conn.execute(f"DELETE FROM activities WHERE customer_id IN {owned}", (pattern,))
    conn.execute(f"DELETE FROM contacts WHERE customer_id IN {owned}", (pattern,))
    conn.execute("DELETE FROM customers WHERE domain LIKE %s", (pattern,))


# Zwei Pfade, zwei Token, Pfadweiche (F08 Etappe 3, ADR 0005 Punkte 3 und 4)
WRITE_PATH = "/mcp-write"
# Seit F08 liegen create_lead und log_activity auf WRITE_PATH, nicht mehr auf MCP_PATH. Beide sind echte Werkzeuge
# (log_activity seit Etappe 4b, create_lead seit Etappe 4c).
WRITE_TOOLS = {"create_lead", "log_activity"}


def body_of_size(
    size: int, tool: str, *, field: str = "query", extra: dict[str, Any] | None = None
) -> bytes:
    """Gültiger JSON-RPC-Aufruf von `tool` mit genau `size` Bytes (nur ASCII), das Argument `field` füllt auf.

    Gemeinsame Hilfe für die Größenprüfungen auf /mcp-write (test_mcp_write_limits.py). Geprüft wird nur die
    Größenbegrenzung der Anfrage, nicht der Inhalt des Arguments. `extra` sind weitere Argumente (z. B. die übrigen
    Pflichtfelder eines Werkzeugs), damit der Aufruf die Eingabeprüfung des Werkzeugs erreicht.
    """

    def build(pad: int) -> bytes:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": tool, "arguments": {field: "x" * pad, **(extra or {})}},
        }
        return json.dumps(payload).encode("ascii")

    overhead = len(build(0))
    body = build(size - overhead)
    assert len(body) == size
    return body


class RecordingApp:
    """ASGI-Fake für die Tests der Weiche: merkt sich jeden Aufruf, liest nie `receive` und antwortet 200 mit ihrem Namen.

    `calls` enthält (scope, receive, send) so, wie die Weiche sie übergeben hat (Identität prüfbar).
    """

    def __init__(self, name: str) -> None:
        self.name = name
        self.calls: list[tuple[Any, Any, Any]] = []

    @property
    def scopes(self) -> list[Any]:
        return [scope for scope, _, _ in self.calls]

    async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
        self.calls.append((scope, receive, send))
        if scope["type"] == "http":
            await send({"type": "http.response.start", "status": 200, "headers": []})
            await send({"type": "http.response.body", "body": self.name.encode()})


def drive_lifespan(app: Any) -> list[dict[str, Any]]:
    """Spielt eine ASGI-Lifespan durch: sendet `lifespan.startup`, nach `startup.complete` `lifespan.shutdown`.

    Gibt alle Nachrichten zurück, die die App gesendet hat. Eine Ausnahme der App wird nicht verschluckt (der Test
    scheitert dann); eine App, die nach fünf Sekunden nicht fertig ist, ebenfalls.
    """
    import asyncio

    async def run() -> list[dict[str, Any]]:
        sent: list[dict[str, Any]] = []
        inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        await inbox.put({"type": "lifespan.startup"})

        async def send(message: dict[str, Any]) -> None:
            sent.append(message)
            if message["type"] == "lifespan.startup.complete":
                await inbox.put({"type": "lifespan.shutdown"})

        scope = {"type": "lifespan", "asgi": {"version": "3.0"}}
        await asyncio.wait_for(app(scope, inbox.get, send), timeout=5)
        return sent

    return asyncio.run(run())


def fake_lifespan(name: str, events: list[str], fail_on: str | None = None) -> Any:
    """Fabrik für einen asynchronen Kontextmanager, der `name:enter` und `name:exit` in `events` aufzeichnet.

    `fail_on` ("<name>:enter" oder "<name>:exit") lässt genau diesen Schritt mit RuntimeError("geheimtext")
    scheitern; beim Start steht dann `<name>:enter-failed` in `events`. Die Weiche ruft die Fabrik je Lauf auf.
    """
    from contextlib import asynccontextmanager

    @asynccontextmanager
    async def factory() -> Any:
        if fail_on == f"{name}:enter":
            events.append(f"{name}:enter-failed")
            raise RuntimeError("geheimtext")
        events.append(f"{name}:enter")
        try:
            yield
        finally:
            events.append(f"{name}:exit")
            if fail_on == f"{name}:exit":
                raise RuntimeError("geheimtext")

    return factory


class CountingDatabase:
    """ConnectionSource, die nie benutzt werden darf: Jeder Zugriff wird gezählt und wirft AssertionError.

    `calls == 0` belegt, dass ein Aufruf die Datenbank nicht berührt hat (z. B. weil die Eingabeprüfung vorher abwies).
    """

    def __init__(self) -> None:
        self.calls = 0

    @contextmanager
    def connection(self) -> Iterator[Any]:
        self.calls += 1
        raise AssertionError("Datenbank berührt")
        yield  # pragma: no cover - macht die Funktion zum Generator


class ExplodingConnection:
    """Jeder Zugriff auf die Verbindung (execute, cursor, ...) wirft die übergebene Ausnahme."""

    def __init__(self, error: BaseException) -> None:
        self._error = error

    def __getattr__(self, name: str) -> Any:
        raise self._error


class ExplodingDatabase:
    """ConnectionSource, deren Verbindung bei jedem Zugriff die übergebene Ausnahme wirft."""

    def __init__(self, error: BaseException) -> None:
        self._error = error

    @contextmanager
    def connection(self) -> Iterator[Any]:
        yield ExplodingConnection(self._error)


def _statement_text(query: Any) -> str:
    if isinstance(query, str):
        return query
    if isinstance(query, bytes):
        return query.decode("utf-8")
    return str(query.as_string())


class _RecordedCursor:
    def __init__(self, inner: Any, record: Any, before: Any) -> None:
        self._inner = inner
        self._record = record
        self._before = before

    def __enter__(self) -> "_RecordedCursor":
        return self

    def __exit__(self, *exc_info: object) -> None:
        self._inner.close()

    def execute(self, query: Any, params: Any = None, **kwargs: Any) -> "_RecordedCursor":
        self._before(_statement_text(query), params)
        self._inner.execute(query, params, **kwargs)
        self._record(_statement_text(query), params)
        return self

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class _RecordedConnection:
    def __init__(self, inner: Any, record: Any, before: Any) -> None:
        self._inner = inner
        self._record = record
        self._before = before

    def execute(self, query: Any, params: Any = None, **kwargs: Any) -> Any:
        self._before(_statement_text(query), params)
        result = self._inner.execute(query, params, **kwargs)
        self._record(_statement_text(query), params)
        return result

    def cursor(self, *args: Any, **kwargs: Any) -> _RecordedCursor:
        return _RecordedCursor(self._inner.cursor(*args, **kwargs), self._record, self._before)

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)


class StatementRecorder:
    """Umhüllt eine ConnectionSource und zeichnet (SQL-Text, Parameter) jedes ausgeführten Befehls auf.

    `on_execute(text, params)` (optional) läuft **nach** jedem Befehl, auf dem Weg des Aufrufers: So lässt sich
    prüfen, was eine zweite Verbindung mitten im Aufruf sieht, oder ein Fehler nach einem Befehl einspritzen.
    `before_execute(text, params)` (optional) läuft **vor** jedem Befehl: So lässt sich ein Konkurrent einfügen, bevor
    der INSERT des Aufrufs läuft (der Befehl scheitert dann mit dem echten Fehler der Datenbank), oder ein Fehler
    anstelle des Befehls einspritzen. Ein Befehl, vor dem der Hook wirft, wird nicht ausgeführt und nicht aufgezeichnet.
    `connections` zählt, wie oft `connection()` geöffnet wurde (= Zahl der Versuche eines Aufrufs).
    """

    def __init__(self, inner: Any, on_execute: Any = None, before_execute: Any = None) -> None:
        self._inner = inner
        self._on_execute = on_execute
        self._before_execute = before_execute
        self.statements: list[tuple[str, Any]] = []
        self.connections = 0

    def _record(self, text: str, params: Any) -> None:
        self.statements.append((text, params))
        if self._on_execute is not None:
            self._on_execute(text, params)

    def _before(self, text: str, params: Any) -> None:
        if self._before_execute is not None:
            self._before_execute(text, params)

    @contextmanager
    def connection(self) -> Iterator[_RecordedConnection]:
        self.connections += 1
        with self._inner.connection() as connection:
            yield _RecordedConnection(connection, self._record, self._before)


# Hilfen für Aufrufe über HTTP an /mcp-write mit einer Datenbank (Ende-zu-Ende-Tests von create_lead).
# `client` ist ein TestClient (e2e_client_for), `headers` enthält das Token.


def rpc_post(
    client: Any,
    headers: dict[str, str],
    method: str,
    params: dict[str, Any] | None = None,
    path: str = WRITE_PATH,
) -> Any:
    """Schickt rohes JSON-RPC an `path` und gibt die HTTP-Antwort zurück (Status wird nicht geprüft)."""
    payload = {"jsonrpc": "2.0", "id": 1, "method": method, "params": params or {}}
    return client.post(path, json=payload, headers={**RPC_HEADERS, **headers})


def tool_call_post(
    client: Any,
    headers: dict[str, str],
    name: str,
    arguments: dict[str, Any],
    path: str = WRITE_PATH,
) -> Any:
    """tools/call für das Werkzeug `name` mit `arguments`; gibt die HTTP-Antwort zurück."""
    return rpc_post(client, headers, "tools/call", {"name": name, "arguments": arguments}, path)


def result_of(response: Any) -> dict[str, Any]:
    """`result` einer JSON-RPC-Antwort mit HTTP 200 (auch bei isError); bricht bei anderem Status ab."""
    assert response.status_code == 200, response.text
    return parse_rpc_body(response)["result"]


# Hilfen fuer F10 (Fortschrittsmeldungen): Ereignisparser, Probe-App, ASGI-Treiber, Loopback-Server.

MODERN_VERSION = "2026-07-28"


def parse_sse_events(text: str) -> list[dict[str, Any]]:
    """Alle data:-Ereignisse eines Ereignisstroms als JSON, in der Reihenfolge des Stroms (Kommentarzeilen wie ': ping' zaehlen nicht).

    Zeilen enden nur an \\r\\n, \\n und \\r (SSE-Spezifikation), nicht an anderen Zeichen wie U+2028, U+2029 oder U+0085, die str.splitlines()
    ebenfalls trennt; solche Zeichen koennen in einem Token stehen.
    """
    return [
        json.loads(line[len("data:") :].strip(" "))
        for line in re.split(r"\r\n|\n|\r", text)
        if line.startswith("data:")
    ]


def sse_progress_event_sizes(content: bytes) -> list[int]:
    """Rohe Groesse (Bytes, Rahmen und Endzeile eingeschlossen) jedes SSE-Ereignisses, das eine Fortschrittsmeldung traegt.

    Ereignisse enden an einer Leerzeile (\\r\\n\\r\\n, \\n\\n oder \\r\\r), wie in der SSE-Spezifikation.
    """
    return [
        len(match.group(0))
        for match in re.finditer(rb"(.*?)(\r\n\r\n|\n\n|\r\r)", content, re.DOTALL)
        if b"notifications/progress" in match.group(1)
    ]


def modern_tool_call(
    tool: str, arguments: dict[str, Any] | None = None, progress_token: Any = None
) -> tuple[dict[str, Any], dict[str, str]]:
    """(JSON-RPC-Nutzlast, Kopfzeilen) eines tools/call auf dem modernen Weg (Header MCP-Protocol-Version = 2026-07-28).

    Der moderne Weg verlangt die Umschlagfelder in params._meta und passende Kopfzeilen (Mcp-Method, Mcp-Name).
    """
    from mcp_types import CLIENT_CAPABILITIES_META_KEY, PROTOCOL_VERSION_META_KEY

    meta: dict[str, Any] = {
        PROTOCOL_VERSION_META_KEY: MODERN_VERSION,
        CLIENT_CAPABILITIES_META_KEY: {},
    }
    if progress_token is not None:
        meta["progressToken"] = progress_token
    payload = {
        "jsonrpc": "2.0",
        "id": 7,
        "method": "tools/call",
        "params": {"name": tool, "arguments": arguments or {}, "_meta": meta},
    }
    headers = {
        **RPC_HEADERS,
        "MCP-Protocol-Version": MODERN_VERSION,
        "Mcp-Method": "tools/call",
        "Mcp-Name": tool,
    }
    return payload, headers


def probe_app(tool: Any, name: str = "probe_progress", server_name: str = "beleg") -> Any:
    """Eine SDK-App (zustandslos, Streaming, Host-Schutz fuer Loopback) mit genau einem Werkzeug des Tests, ohne Paketcode.

    `tool` ist eine Funktion mit Parameter `ctx: Context` (die Annotation muss ein echtes Objekt sein, kein Text).
    """
    from mcp.server.mcpserver import MCPServer
    from mcp.server.transport_security import TransportSecuritySettings

    server = MCPServer(server_name)
    server.tool(name=name)(tool)
    return server.streamable_http_app(
        streamable_http_path=MCP_PATH,
        json_response=False,
        stateless_http=True,
        transport_security=TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=["127.0.0.1:*"],
            allowed_origins=["http://127.0.0.1:*"],
        ),
        host="127.0.0.1",
    )


class AsgiResponse:
    """Die Antwort eines AsgiRunner.post: alle gesendeten ASGI-Nachrichten in der Reihenfolge des Sendens."""

    def __init__(self) -> None:
        self.messages: list[dict[str, Any]] = []

    @property
    def status(self) -> int:
        start = [m for m in self.messages if m["type"] == "http.response.start"]
        assert start, "keine Antwort begonnen"
        return int(start[0]["status"])

    @property
    def chunks(self) -> list[bytes]:
        return [m.get("body", b"") for m in self.messages if m["type"] == "http.response.body"]

    @property
    def text(self) -> str:
        return b"".join(self.chunks).decode("utf-8")


class AsgiRunner:
    """Treibt eine ASGI-App ohne Port: Lifespan laeuft als Aufgabe, jeder Aufruf von `post` ist eine Anfrage.

    Nur in einer laufenden asyncio-Schleife benutzen (asyncio.run). `post` ruft fuer jede gesendete Nachricht erst
    die Aufzeichnung und dann `on_send` auf; `on_send` darf warten und so einen Client nachbilden, der nicht liest.
    Nach dem Antwortende meldet `receive` die Trennung (http.disconnect), wie ein Server es tut.
    """

    def __init__(self, app: Any) -> None:
        self._app = app
        self._state: dict[str, Any] = {}

    async def __aenter__(self) -> Self:
        import asyncio

        self._inbox: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        self._started = asyncio.Event()
        self._stopped = asyncio.Event()
        self._failed: list[dict[str, Any]] = []

        async def send(message: dict[str, Any]) -> None:
            kind = message["type"]
            if kind == "lifespan.startup.complete":
                self._started.set()
            elif kind == "lifespan.shutdown.complete":
                self._stopped.set()
            elif kind.endswith(".failed"):
                self._failed.append(message)
                self._started.set()
                self._stopped.set()

        scope = {"type": "lifespan", "asgi": {"version": "3.0"}, "state": self._state}
        self._task = asyncio.create_task(self._app(scope, self._inbox.get, send))
        await self._inbox.put({"type": "lifespan.startup"})
        await asyncio.wait_for(self._started.wait(), timeout=10)
        assert not self._failed, "Lifespan-Start gescheitert"
        return self

    async def __aexit__(self, *exc_info: object) -> None:
        import asyncio

        await self._inbox.put({"type": "lifespan.shutdown"})
        await asyncio.wait_for(self._stopped.wait(), timeout=10)
        await asyncio.wait_for(self._task, timeout=10)

    async def post(
        self,
        path: str,
        payload: dict[str, Any],
        headers: dict[str, str],
        *,
        on_send: Any = None,
        disconnect: Any = None,
    ) -> AsgiResponse:
        """Eine POST-Anfrage; `disconnect` (asyncio.Event) laesst den Client mitten im Aufruf gehen (http.disconnect)."""
        import asyncio

        body = json.dumps(payload).encode("ascii")
        raw_headers = [
            (b"host", b"127.0.0.1:8000"),
            (b"content-length", str(len(body)).encode("ascii")),
            *((k.lower().encode("latin-1"), v.encode("latin-1")) for k, v in headers.items()),
        ]
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "POST",
            "scheme": "http",
            "path": path,
            "raw_path": path.encode("ascii"),
            "query_string": b"",
            "root_path": "",
            "headers": raw_headers,
            "client": ("127.0.0.1", 50000),
            "server": ("127.0.0.1", 8000),
            "state": self._state,
        }
        response = AsgiResponse()
        delivered = False
        finished = asyncio.Event()

        async def receive() -> dict[str, Any]:
            nonlocal delivered
            if not delivered:
                delivered = True
                return {"type": "http.request", "body": body, "more_body": False}
            waiting = [asyncio.ensure_future(finished.wait())]
            if disconnect is not None:
                waiting.append(asyncio.ensure_future(disconnect.wait()))
            _, pending = await asyncio.wait(waiting, return_when=asyncio.FIRST_COMPLETED)
            for task in pending:
                task.cancel()
            return {"type": "http.disconnect"}

        async def send(message: dict[str, Any]) -> None:
            response.messages.append(message)
            if on_send is not None:
                await on_send(message)
            if message["type"] == "http.response.body" and not message.get("more_body", False):
                finished.set()

        try:
            await self._app(scope, receive, send)
        finally:
            finished.set()
        return response


class SocketsUnavailable(OSError):
    """Die Umgebung erlaubt keinen Socket auf 127.0.0.1 (Test wird lokal uebersprungen, in GitHub Actions ist es ein Fehler)."""


def _run_server_on_selector_loop(server: Any, sock: Any) -> None:
    """Fuehrt `server` (uvicorn.Server) mit einer SelectorEventLoop aus, nicht mit der Standardschleife von uvicorn.

    Grund (belegt in F10, Diagnose von serve_on_loopback): Unter Windows ist die Standardschleife die ProactorEventLoop.
    Ihr `_call_connection_lost` ruft `sock.shutdown()` in einem `finally` auf; scheitert es mit ConnectionResetError
    (der Client hat die Verbindung zurueckgesetzt), bleiben `sock.close()` und `Server._detach` aus, der asyncio.Server
    zaehlt die Verbindung weiter, und `Server.wait_closed()` (uvicorn.Server.shutdown) wartet ohne Ende, noch vor dem
    Lifespan-Ende. uvicorn selbst setzt dasselbe wie `Server.run` ein (asyncio.Runner mit loop_factory); Signale braucht der
    Thread nicht (capture_signals tut ausserhalb des Hauptthreads nichts). Auf Linux ist die Selector-Schleife ohnehin Standard.
    """
    import asyncio

    with asyncio.Runner(loop_factory=asyncio.SelectorEventLoop) as runner:
        runner.run(server.serve(sockets=[sock]))


def _shutdown_report(server: Any, loop_holder: list[Any], elapsed: float) -> str:
    """Kompakter Bericht eines haengenden Herunterfahrens: verstrichene Zeit, Namen offener Tasks, uvicorn-Zaehler.

    Liest von einem anderen Thread aus (Momentaufnahme); jeder Teil faengt seine eigenen Fehler ab, damit der Bericht nie
    die eigentliche Meldung verdeckt. Enthaelt nur Namen und Zaehler, keine Werte.
    """
    import asyncio

    lines = [f"Herunterfahren haengt nach {elapsed:.1f} s"]
    try:
        loop = loop_holder[0] if loop_holder else None
        tasks = list(asyncio.all_tasks(loop)) if loop is not None else []
        lines.append(f"Offene asyncio-Tasks ({len(tasks)}): {[task.get_name() for task in tasks]}")
    except Exception as error:  # noqa: BLE001 - Diagnose darf nie die Meldung verdecken
        lines.append(f"Tasks nicht lesbar: {type(error).__name__}")
    try:
        state = server.server_state
        lines.append(
            f"uvicorn: Verbindungen={len(state.connections)} Tasks={len(state.tasks)} "
            f"should_exit={server.should_exit} force_exit={server.force_exit}"
        )
    except Exception as error:  # noqa: BLE001
        lines.append(f"uvicorn-Zustand nicht lesbar: {type(error).__name__}")
    return "\n".join(lines)


@contextmanager
def serve_on_loopback(app: Any) -> Iterator[str]:
    """Startet `app` mit uvicorn in einem Thread auf 127.0.0.1 (Port 0, Socket vorab gebunden) und gibt die Basis-URL zurueck.

    Ereignisbasiert: Der Start wird ueber das Lifespan-Ereignis `startup.complete` gemeldet, nicht ueber Schlafzeiten.
    Jede Wartezeit hat ein hartes Zeitlimit. Das Ende (auch bei einem Fehler im Test) setzt `should_exit`, wartet auf den
    Thread und erzwingt danach das Ende. Wirft SocketsUnavailable, wenn der Socket nicht gebunden werden kann.

    Die Testhilfe laeuft bewusst auf der SelectorEventLoop (siehe `_run_server_on_selector_loop`). Belegte Ursache eines
    frueheren Hangs beim Beenden (F10, nur Windows, ca. 25 % der Laeufe, 15,8 s): Die ProactorEventLoop ruft in
    `_call_connection_lost` `sock.shutdown()` im `finally` auf; ein ConnectionResetError ueberspringt `sock.close()` und
    `Server._detach`, der asyncio.Server zaehlt die Verbindung weiter, `Server.wait_closed()` haengt in
    `uvicorn.Server.shutdown` und `lifespan.shutdown` wird nie gesendet. `force_exit` hilft nicht (es betrifft
    `wait_closed` nicht). Haengt das Beenden trotzdem, nennt die Assertion die verstrichene Zeit, die Namen offener Tasks
    und die uvicorn-Zaehler.
    """
    import socket
    import threading

    import uvicorn

    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        sock.bind(("127.0.0.1", 0))
    except OSError:
        sock.close()
        raise SocketsUnavailable("Kein Socket auf 127.0.0.1 moeglich.") from None
    port = sock.getsockname()[1]
    started = threading.Event()
    outcome: list[str] = []
    loop_holder: list[Any] = []

    class Watched:
        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            if scope["type"] != "lifespan":
                await app(scope, receive, send)
                return
            import asyncio

            loop_holder.append(asyncio.get_running_loop())  # laeuft im Server-Thread

            async def watching(message: dict[str, Any]) -> None:
                if message["type"] in ("lifespan.startup.complete", "lifespan.startup.failed"):
                    outcome.append(message["type"])
                    started.set()
                await send(message)

            await app(scope, receive, watching)

    config = uvicorn.Config(Watched(), log_level="warning", access_log=False, lifespan="on")
    server = uvicorn.Server(config)
    thread = threading.Thread(target=_run_server_on_selector_loop, args=(server, sock), daemon=True)
    thread.start()
    try:
        if not started.wait(timeout=10) or outcome != ["lifespan.startup.complete"]:
            raise RuntimeError("Der Loopback-Server ist nicht gestartet.")
        yield f"http://127.0.0.1:{port}"
    finally:
        import time
        import warnings

        server.should_exit = True
        began = time.monotonic()
        reports: list[str] = []
        # Erste Frist 3 s: Dauert das Beenden laenger, wird der Zustand sofort festgehalten (vor force_exit).
        thread.join(timeout=3)
        if thread.is_alive():
            reports.append(_shutdown_report(server, loop_holder, time.monotonic() - began))
            thread.join(timeout=7)
        if thread.is_alive():
            server.force_exit = True
            thread.join(timeout=5)
        sock.close()
        elapsed = time.monotonic() - began
        if reports and not thread.is_alive():
            warnings.warn(
                f"Der Loopback-Server brauchte {elapsed:.1f} s zum Beenden.\n" + "\n".join(reports),
                RuntimeWarning,
                stacklevel=2,
            )
        assert not thread.is_alive(), (
            f"Der Loopback-Server wurde nicht beendet ({elapsed:.1f} s).\n" + "\n".join(reports)
        )


def serve_or_skip(stack: Any, app: Any) -> str:
    """Startet `app` auf Loopback (im ExitStack `stack`) und gibt die Basis-URL zurueck.

    Erlaubt die Umgebung keinen Socket, wird der Test lokal uebersprungen; in GitHub Actions ist das ein Fehler (Muster wie
    TEST_DATABASE_URL), damit die Pipeline nie gruen ist, ohne dass die Port-Tests liefen.
    """
    import os

    import pytest

    unavailable = False
    try:
        base = stack.enter_context(serve_on_loopback(app))
    except SocketsUnavailable:
        unavailable = True
    if unavailable:
        if os.environ.get("GITHUB_ACTIONS") == "true":
            pytest.fail(
                "Kein Socket auf 127.0.0.1 in GitHub Actions: Die Port-Tests duerfen dort nicht uebersprungen werden.",
                pytrace=False,
            )
        pytest.skip("Kein Socket auf 127.0.0.1 moeglich: Port-Tests werden uebersprungen.")
    return str(base)


# Hilfen fuer die Tests der Fortschrittsmeldungen mit den echten Werkzeugen (F10): Datenbank-Doubles mit Zeitleiste.


class _ScriptedCursor:
    def __init__(self, owner: "ScriptedDatabase") -> None:
        self._owner = owner
        self._rows: list[dict[str, Any]] = []

    def __enter__(self) -> Self:
        return self

    def __exit__(self, *exc_info: object) -> None:
        return None

    def execute(self, query: Any, params: Any = None, **kwargs: Any) -> Self:
        self._rows = self._owner.run(str(query), params)
        return self

    def fetchall(self) -> list[dict[str, Any]]:
        return list(self._rows)

    def fetchone(self) -> dict[str, Any] | None:
        return self._rows[0] if self._rows else None


class _ScriptedConnection:
    def __init__(self, owner: "ScriptedDatabase") -> None:
        self._owner = owner

    def cursor(self, *args: Any, **kwargs: Any) -> _ScriptedCursor:
        return _ScriptedCursor(self._owner)

    def execute(self, query: Any, params: Any = None, **kwargs: Any) -> _ScriptedCursor:
        return _ScriptedCursor(self._owner).execute(query, params)


class ScriptedDatabase:
    """ConnectionSource ohne Datenbank: `handler(text, params)` liefert die Zeilen, `timeline` haelt die Ereignisse fest.

    Zeitleiste (Texte): "open" (Verbindung geoeffnet), "sql:SELECT" / "sql:INSERT" (je Befehl, erstes Wort), und am Ende
    des Blocks "commit" (write=True) oder "close" (write=False), bei einer Ausnahme "rollback". `before_statement(text)`
    laeuft vor jedem Befehl (darf warten oder werfen; ein Befehl, vor dem es wirft, steht nicht in der Zeitleiste).
    `on_end(kind)` laeuft am Ende mit "commit", "close" oder "rollback".
    """

    def __init__(
        self,
        handler: Any,
        timeline: list[str],
        *,
        write: bool = False,
        before_statement: Any = None,
        on_end: Any = None,
    ) -> None:
        self._handler = handler
        self.timeline = timeline
        self._write = write
        self._before = before_statement
        self._on_end = on_end

    def run(self, text: str, params: Any) -> list[dict[str, Any]]:
        if self._before is not None:
            self._before(text)
        self.timeline.append(f"sql:{text.split()[0].upper()}")
        rows = self._handler(text, params)
        return [] if rows is None else rows

    @contextmanager
    def connection(self) -> Iterator[_ScriptedConnection]:
        self.timeline.append("open")
        try:
            yield _ScriptedConnection(self)
        except BaseException:
            self.timeline.append("rollback")
            if self._on_end is not None:
                self._on_end("rollback")
            raise
        kind = "commit" if self._write else "close"
        self.timeline.append(kind)
        if self._on_end is not None:
            self._on_end(kind)


class SpyProgress:
    """Ersatz fuer den Reporter: haelt jeden `step` als "progress:<NAME>" in derselben Zeitleiste fest wie die Datenbank."""

    def __init__(self, timeline: list[str]) -> None:
        self.timeline = timeline

    def step(self, step: Any) -> None:
        self.timeline.append(f"progress:{step.name}")


def customer_handler(text: str, params: Any) -> list[dict[str, Any]]:
    """Antworten fuer find_customer: bekannt ist nur die Adresse erika@muster.example; Firma und Domain sind unbekannt."""
    from datetime import UTC, datetime

    from hoffmann_data import crm

    if text == crm.CUSTOMER_BY_EMAIL_SQL:
        return [
            {
                "id": 1,
                "company_name": "Muster GmbH",
                "domain": "muster.example",
                "industry": "other",
                "country": "de",
                "status": "existing",
            }
        ]
    if text in (crm.CUSTOMER_BY_COMPANY_SQL, crm.CUSTOMER_BY_DOMAIN_SQL):
        return []
    if text == crm.CONTACTS_SQL:
        return [
            {
                "first_name": "Erika",
                "last_name": "Muster",
                "email": "erika@muster.example",
                "job_title": None,
                "language": "de",
            }
        ]
    if text == crm.ACTIVITIES_SQL:
        return [
            {
                "type": "inquiry",
                "occurred_at": datetime(2026, 3, 1, 10, 0, tzinfo=UTC),
                "subject": "Anfrage",
                "summary": "Zusammenfassung",
                "amount_eur": None,
                "created_by": "seed",
                "article_number": None,
            }
        ]
    raise AssertionError(f"unerwarteter Befehl: {text[:40]!r}")


def make_write_handler(
    matches: tuple[int | None, int | None, int | None] = (None, None, None),
    customer_known: bool = True,
) -> Any:
    """Antworten fuer create_lead und log_activity. `matches` = (Kunde der Domain, Kunde des Namens, Kunde der Adresse)."""

    def handler(text: str, params: Any) -> list[dict[str, Any]]:
        from hoffmann_data import db

        if text == db.FIND_LEAD_MATCHES_SQL:
            return [
                {
                    "domain_customer_id": matches[0],
                    "name_customer_id": matches[1],
                    "contact_customer_id": matches[2],
                }
            ]
        if text == db.INSERT_CUSTOMER_SQL:
            return [{"id": 41}]
        if text in (db.INSERT_CONTACT_SQL, db.INSERT_ACTIVITY_SQL):
            return []
        if text == db.FIND_CUSTOMER_ID_SQL:
            return [{"id": 7}] if customer_known else []
        if text == db.FIND_CONTACT_ID_SQL:
            return [{"id": 8}]
        raise AssertionError(f"unerwarteter Befehl: {text[:40]!r}")

    return handler


LEAD_ARGUMENTS: dict[str, Any] = {
    "company_name": "Neu GmbH",
    "contact_first_name": "Erika",
    "contact_last_name": "Muster",
    "contact_email": "erika@neu-gmbh.example",
    "language": "de",
    "country": "de",
}
ACTIVITY_ARGUMENTS: dict[str, Any] = {
    "customer_domain": "muster.example",
    "type": "note",
    "subject": "Betreff",
    "summary": "Zusammenfassung",
}


def tool_call_request(
    era: str,
    tool: str,
    arguments: dict[str, Any],
    *,
    progress_token: Any = None,
    authorization: str | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """(Nutzlast, Kopfzeilen) eines tools/call auf dem alten ("alt") oder modernen ("modern") Weg, mit Token der Anmeldung."""
    if era == "modern":
        payload, headers = modern_tool_call(tool, arguments, progress_token)
    else:
        params: dict[str, Any] = {"name": tool, "arguments": arguments}
        if progress_token is not None:
            params["_meta"] = {"progressToken": progress_token}
        payload = {"jsonrpc": "2.0", "id": 7, "method": "tools/call", "params": params}
        headers = dict(RPC_HEADERS)
    if authorization is not None:
        headers["Authorization"] = f"Bearer {authorization}"
    return payload, headers


def progress_events(events: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Nur die Fortschrittsmeldungen eines Stroms (notifications/progress), in der Reihenfolge des Stroms."""
    return [e for e in events if e.get("method") == "notifications/progress"]
