"""Tests der Schnittstellen je Pfad im Doppelbetrieb (F06 und F08, ADR 0005 Punkte 3 und 4).

Festlegungen:
- /mcp (Lese-Token) listet drei Lese-Werkzeuge, beide Resources und den Prompt; /mcp-write (Schreib-Token) listet nur
  create_lead und log_activity, beide bis Etappe 4 Platzhalter (fester Fehler "noch nicht implementiert").
- Jedes Werkzeug ist auf dem anderen Pfad unbekannt (Ergebnis mit isError, kein Protokollfehler, nie der Platzhalterfehler
  und nie ein Datenbankzugriff). Der Schreib-Server hat keine Resources und keinen Prompt.
- Auslegung von F06 ("listet seine Schnittstellen auf", ADR 0005, ADR-Eintrag folgt): Jeder Pfad listet seine
  Schnittstellen, beide zusammen genau die acht aus Auftrag 6.4; die Vereinigung belegt test_both_paths_together_....
- Der Lese-Server nennt die Schreib-Werkzeuge nirgends (ADR 0005 Punkt 4: Der Agent sieht sie nicht); jeder Server
  nennt in seinen instructions nur die eigenen Werkzeuge. Die zwei Server teilen keinen Zustand.

Ohne Datenbank. Rot, bis server.py create_read_server, create_write_server und die Weiche kennt (TDD).
"""

import asyncio
import json
from collections.abc import Callable
from typing import Any

import pytest
from mcp_testkit import (
    NOT_IMPLEMENTED,
    PROMPT_NAMES,
    READ_TOOLS,
    RESOURCE_URIS,
    TOOL_NAMES,
    WRITE_TOOLS,
)

Rpc = Callable[..., dict[str, Any]]
INITIALIZE = {
    "protocolVersion": "2025-06-18",
    "capabilities": {},
    "clientInfo": {"name": "test", "version": "1"},
}
READ_ARGUMENTS = {
    "search_products": {"query": "Gurtband"},
    "get_product": {"article_number": "FB-1001"},
    "find_customer": {"query": "firma.example"},
}


def _tools(rpc: Rpc) -> dict[str, dict[str, Any]]:
    return {tool["name"]: tool for tool in rpc("tools/list")["result"]["tools"]}


def _listed(body: dict[str, Any], key: str) -> list[Any]:
    """Die Liste einer Antwort; ein Protokollfehler ("nicht unterstützt") zählt als leer."""
    if "error" in body:
        return []
    return list(body["result"][key])


def _text(result: dict[str, Any]) -> str:
    return " ".join(c.get("text", "") for c in result["content"])


# Listen je Pfad


def test_tools_list_on_the_write_path_has_exactly_the_two_write_tools(rpc_write: Rpc) -> None:
    assert set(_tools(rpc_write)) == WRITE_TOOLS


def test_tools_list_on_the_read_path_does_not_show_the_write_tools(rpc_read: Rpc) -> None:
    names = set(_tools(rpc_read))
    assert names == READ_TOOLS
    assert not names & WRITE_TOOLS


def test_both_paths_together_list_all_eight_interfaces_with_a_description(
    rpc_read: Rpc, rpc_write: Rpc
) -> None:
    """Die Abnahme von F06 für beide Endpunkte zusammen: genau die acht Schnittstellen aus Auftrag 6.4."""
    read_tools = rpc_read("tools/list")["result"]["tools"]
    resources = rpc_read("resources/list")["result"]["resources"]
    prompts = rpc_read("prompts/list")["result"]["prompts"]
    write_tools = rpc_write("tools/list")["result"]["tools"]
    items = [*read_tools, *resources, *prompts, *write_tools]
    assert len(items) == 8
    assert all((item.get("description") or "").strip() for item in items)
    assert {t["name"] for t in [*read_tools, *write_tools]} == TOOL_NAMES
    assert {str(r["uri"]) for r in resources} == RESOURCE_URIS
    assert {p["name"] for p in prompts} == PROMPT_NAMES


def test_the_write_server_has_no_resources_and_no_prompts(rpc_write: Rpc) -> None:
    """Belegt das Verhalten des SDKs: leere Liste oder Protokollfehler, nie die Einträge des Lese-Servers."""
    assert _listed(rpc_write("resources/list"), "resources") == []
    assert _listed(rpc_write("resources/templates/list"), "resourceTemplates") == []
    assert _listed(rpc_write("prompts/list"), "prompts") == []


# Jedes Werkzeug ist auf dem anderen Pfad unbekannt


@pytest.mark.parametrize("tool", sorted(READ_TOOLS))
def test_read_tools_are_unknown_on_the_write_path(rpc_write: Rpc, tool: str) -> None:
    from hoffmann_data.db import DATABASE_ERROR, NOT_CONFIGURED

    body = rpc_write("tools/call", {"name": tool, "arguments": READ_ARGUMENTS[tool]})
    assert "error" not in body
    result = body["result"]
    assert result["isError"] is True
    assert not result.get("structuredContent")
    dumped = json.dumps(body, ensure_ascii=False)
    for text in (NOT_IMPLEMENTED, NOT_CONFIGURED, DATABASE_ERROR):
        assert text not in dumped, "das Werkzeug lief: Es darf auf /mcp-write nicht existieren"


@pytest.mark.parametrize("tool", sorted(WRITE_TOOLS))
def test_write_tools_are_unknown_on_the_read_path_in_dual_operation(
    rpc_read: Rpc, tool: str
) -> None:
    body = rpc_read("tools/call", {"name": tool, "arguments": {}})
    assert "error" not in body
    assert body["result"]["isError"] is True
    assert NOT_IMPLEMENTED not in json.dumps(body, ensure_ascii=False)


@pytest.mark.parametrize("uri", sorted(RESOURCE_URIS))
def test_resources_are_unknown_on_the_write_path(rpc_write: Rpc, uri: str) -> None:
    body = rpc_write("resources/read", {"uri": uri})
    assert "error" in body and "result" not in body
    assert NOT_IMPLEMENTED not in json.dumps(body, ensure_ascii=False)


def test_the_prompt_is_unknown_on_the_write_path(rpc_write: Rpc) -> None:
    body = rpc_write("prompts/get", {"name": "antwort_entwurf", "arguments": {}})
    assert "error" in body and "result" not in body
    assert NOT_IMPLEMENTED not in json.dumps(body, ensure_ascii=False)


def test_an_unknown_tool_on_the_write_path_is_a_tool_error_result(rpc_write: Rpc) -> None:
    body = rpc_write("tools/call", {"name": "send_email", "arguments": {}})
    assert "error" not in body
    assert body["result"]["isError"] is True
    assert NOT_IMPLEMENTED not in json.dumps(body, ensure_ascii=False)


# Die Schreib-Werkzeuge (Platzhalter bis Etappe 4); zogen von test_mcp_interfaces.py hierher um


@pytest.mark.parametrize("tool", sorted(WRITE_TOOLS))
def test_each_write_tool_placeholder_returns_an_error_not_data(rpc_write: Rpc, tool: str) -> None:
    result = rpc_write("tools/call", {"name": tool, "arguments": {}})["result"]
    assert result["isError"] is True
    assert NOT_IMPLEMENTED in _text(result)
    assert not result.get("structuredContent")


@pytest.mark.parametrize("tool", sorted(WRITE_TOOLS))
def test_the_write_tools_are_described_as_placeholders(rpc_write: Rpc, tool: str) -> None:
    """create_lead und log_activity bekommen ihr Verhalten in Etappe 4; bis dahin sagt die Beschreibung Platzhalter."""
    description = _tools(rpc_write)[tool]["description"]
    assert description.strip()
    assert "platzhalter" in description.lower()


@pytest.mark.parametrize("tool", sorted(WRITE_TOOLS))
def test_the_placeholder_error_does_not_echo_arguments(rpc_write: Rpc, tool: str) -> None:
    """Der Platzhalterfehler gibt keine Eingabewerte aus (Kundentext ist Daten, kein Echo)."""
    body = rpc_write("tools/call", {"name": tool, "arguments": {"query": "GEHEIM-4711"}})
    dumped = json.dumps(body, ensure_ascii=False)
    assert NOT_IMPLEMENTED in dumped, "es kam nicht der Platzhalterfehler"
    assert "GEHEIM-4711" not in dumped


def test_no_write_tool_can_send_or_delete(rpc_write: Rpc) -> None:
    """Auftrag 5.1: Der Datenservice stellt keine Funktionen zum Versenden oder Löschen bereit."""
    names = {name.lower() for name in _tools(rpc_write)}
    forbidden = ("send", "mail", "delete", "remove", "drop", "update", "overwrite")
    assert not [n for n in names if any(word in n for word in forbidden)]


# Was jeder Server über sich sagt


def test_the_read_server_names_no_write_tool_anywhere(rpc_read: Rpc) -> None:
    """ADR 0005 Punkt 4: Der Agent bekommt nur das Lese-Token und sieht die Schreib-Werkzeuge nicht."""
    instructions = rpc_read("initialize", INITIALIZE)["result"].get("instructions") or ""
    texts = [instructions]
    for key, method in (
        ("tools", "tools/list"),
        ("resources", "resources/list"),
        ("prompts", "prompts/list"),
    ):
        texts += [(item.get("description") or "") for item in rpc_read(method)["result"][key]]
    joined = "\n".join(texts).lower()
    for tool in WRITE_TOOLS:
        assert tool not in joined, tool


def test_the_instructions_of_each_server_name_only_its_own_tools(
    rpc_read: Rpc, rpc_write: Rpc
) -> None:
    read_instructions = (
        rpc_read("initialize", INITIALIZE)["result"].get("instructions") or ""
    ).lower()
    write_instructions = (
        rpc_write("initialize", INITIALIZE)["result"].get("instructions") or ""
    ).lower()
    assert read_instructions.strip() and write_instructions.strip()
    assert read_instructions != write_instructions
    assert all(tool not in read_instructions for tool in WRITE_TOOLS)
    assert all(tool not in write_instructions for tool in READ_TOOLS)


# Kein gemeinsamer Zustand


def test_the_two_servers_share_no_state() -> None:
    from hoffmann_data.server import create_read_server, create_write_server

    async def names(server: Any) -> set[str]:
        return {tool.name for tool in await server.list_tools()}

    read, write = create_read_server(None), create_write_server(None)
    assert read is not write
    assert asyncio.run(names(read)) == READ_TOOLS
    assert asyncio.run(names(write)) == WRITE_TOOLS
    # Ein zweiter Aufruf der Fabrik gibt eine neue, gleich gebaute Instanz, keine geteilte
    other_read, other_write = create_read_server(None), create_write_server(None)
    assert other_read is not read and other_write is not write
    assert asyncio.run(names(other_read)) == READ_TOOLS
    assert asyncio.run(names(other_write)) == WRITE_TOOLS
    assert asyncio.run(names(read)) == READ_TOOLS, "der zweite Bau ändert die erste Instanz nicht"
