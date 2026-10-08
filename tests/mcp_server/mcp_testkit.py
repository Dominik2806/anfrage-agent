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
