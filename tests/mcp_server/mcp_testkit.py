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
from typing import Any

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
# Tabellen, die die Rolle lesen darf (discount_rules bewusst nicht, das kommt mit F09)
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
# Platzhalter sind nur noch beide Resources und der Prompt auf /mcp (NOT_IMPLEMENTED).
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
