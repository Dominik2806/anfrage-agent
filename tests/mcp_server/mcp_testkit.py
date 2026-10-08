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
]
