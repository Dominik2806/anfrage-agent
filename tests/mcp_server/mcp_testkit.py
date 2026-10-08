"""Gemeinsame Konstanten und Hilfen der Tests von hoffmann-data (F06).

Eigenes Modul statt "from conftest import ...": In tests/ liegen mehrere conftest.py (db, seed,
mcp_server), ein Import über den Namen "conftest" könnte die falsche Datei treffen.
"""

import json
import sys
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
MCP_SERVER_DIR = ROOT / "mcp-server"
if str(MCP_SERVER_DIR) not in sys.path:
    sys.path.insert(0, str(MCP_SERVER_DIR))

BASE_URL = "http://127.0.0.1:8000"
MCP_PATH = "/mcp"

TOOL_NAMES = {"search_products", "get_product", "find_customer", "create_lead", "log_activity"}
RESOURCE_URIS = {"policy://tonalitaet", "policy://rabatte"}
PROMPT_NAMES = {"antwort_entwurf"}
NOT_IMPLEMENTED = "noch nicht implementiert"

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
