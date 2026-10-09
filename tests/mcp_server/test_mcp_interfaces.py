"""Tests der acht Schnittstellen (F06, F07) über HTTP mit gültigem Token, mit rohem JSON-RPC.

Der Weg geht durch Middleware, SDK-App und Transport. mcp.Client in-process würde die Auth umgehen.
Seit F07 sind die drei Lese-Werkzeuge echt (Verhalten: test_search_products.py, test_get_product.py,
test_find_customer.py). Die übrigen fünf Schnittstellen (create_lead, log_activity, beide Resources, der
Prompt) bleiben Platzhalter und sind parameterlos, deshalb genügen leere Argumente. Hier stehen die
Prüfungen, die keine Datenbank brauchen: Liste, Beschreibungen und Schemas der Werkzeuge.
"""

import json
from collections.abc import Callable
from typing import Any

import pytest
from mcp_testkit import (
    INTERNAL_ERROR_CODE,
    NOT_IMPLEMENTED,
    PLACEHOLDER_TOOLS,
    PROMPT_NAMES,
    READ_TOOLS,
    RESOURCE_URIS,
    TOOL_NAMES,
)

Rpc = Callable[..., dict[str, Any]]


def test_tools_list_has_exactly_the_five_tools(rpc: Rpc) -> None:
    body = rpc("tools/list")
    assert {t["name"] for t in body["result"]["tools"]} == TOOL_NAMES


def test_resources_list_has_exactly_the_two_policies(rpc: Rpc) -> None:
    body = rpc("resources/list")
    assert {str(r["uri"]) for r in body["result"]["resources"]} == RESOURCE_URIS


def test_resource_templates_list_is_empty(rpc: Rpc) -> None:
    assert rpc("resources/templates/list")["result"]["resourceTemplates"] == []


def test_prompts_list_has_exactly_antwort_entwurf(rpc: Rpc) -> None:
    body = rpc("prompts/list")
    assert {p["name"] for p in body["result"]["prompts"]} == PROMPT_NAMES


def test_all_eight_interfaces_have_a_description(rpc: Rpc) -> None:
    tools = rpc("tools/list")["result"]["tools"]
    resources = rpc("resources/list")["result"]["resources"]
    prompts = rpc("prompts/list")["result"]["prompts"]
    items = [*tools, *resources, *prompts]
    assert len(items) == 8
    assert all((item.get("description") or "").strip() for item in items)


def test_no_tool_can_send_or_delete(rpc: Rpc) -> None:
    """Auftrag 5.1: Der Datenservice stellt keine Funktionen zum Versenden oder Löschen bereit."""
    names = {t["name"].lower() for t in rpc("tools/list")["result"]["tools"]}
    forbidden = ("send", "mail", "delete", "remove", "drop", "update", "overwrite")
    assert not [n for n in names if any(word in n for word in forbidden)]


def test_tool_groups_cover_the_five_tools_without_overlap() -> None:
    assert READ_TOOLS | PLACEHOLDER_TOOLS == TOOL_NAMES
    assert not READ_TOOLS & PLACEHOLDER_TOOLS


@pytest.mark.parametrize("tool", sorted(PLACEHOLDER_TOOLS))
def test_each_tool_placeholder_returns_an_error_not_data(rpc: Rpc, tool: str) -> None:
    body = rpc("tools/call", {"name": tool, "arguments": {}})
    result = body["result"]
    assert result["isError"] is True
    text = " ".join(c.get("text", "") for c in result["content"])
    assert NOT_IMPLEMENTED in text
    assert not result.get("structuredContent")


@pytest.mark.parametrize("uri", sorted(RESOURCE_URIS))
def test_each_resource_placeholder_returns_an_error_not_data(rpc: Rpc, uri: str) -> None:
    body = rpc("resources/read", {"uri": uri})
    assert "error" in body and "result" not in body
    assert body["error"]["code"] == INTERNAL_ERROR_CODE
    assert NOT_IMPLEMENTED in body["error"]["message"]


def test_prompt_placeholder_returns_an_error_not_data(rpc: Rpc) -> None:
    """Das SDK reicht nur MCPError mit Text durch; jede andere Ausnahme wird ersetzt."""
    body = rpc("prompts/get", {"name": "antwort_entwurf", "arguments": {}})
    assert "error" in body and "result" not in body
    assert body["error"]["code"] == INTERNAL_ERROR_CODE
    assert NOT_IMPLEMENTED in body["error"]["message"]


def test_unknown_tool_is_a_tool_error_result(rpc: Rpc) -> None:
    """Das SDK meldet ein unbekanntes Werkzeug als Ergebnis mit isError, nicht als Protokollfehler."""
    body = rpc("tools/call", {"name": "send_email", "arguments": {}})
    assert "error" not in body
    assert body["result"]["isError"] is True
    assert NOT_IMPLEMENTED not in json.dumps(body, ensure_ascii=False)


def test_error_text_does_not_echo_arguments(rpc: Rpc) -> None:
    """Der Platzhalterfehler gibt keine Eingabewerte aus (Kundentext ist Daten, kein Echo)."""
    body = rpc("tools/call", {"name": "log_activity", "arguments": {"query": "GEHEIM-4711"}})
    dumped = json.dumps(body, ensure_ascii=False)
    assert NOT_IMPLEMENTED in dumped, "es kam nicht der Platzhalterfehler"
    assert "GEHEIM-4711" not in dumped


# Die drei Lese-Werkzeuge (F07): Beschreibung und Schemas


def _tools(rpc: Rpc) -> dict[str, dict[str, Any]]:
    return {tool["name"]: tool for tool in rpc("tools/list")["result"]["tools"]}


def _property_types(schema: dict[str, Any], name: str) -> set[str]:
    """Die JSON-Typen einer Eigenschaft, auch wenn sie als anyOf angegeben ist."""
    prop = schema["properties"][name]
    candidates = [prop, *prop.get("anyOf", [])]
    return {c["type"] for c in candidates if "type" in c}


@pytest.mark.parametrize("tool", sorted(READ_TOOLS))
def test_read_tools_are_no_longer_described_as_placeholders(rpc: Rpc, tool: str) -> None:
    description = _tools(rpc)[tool]["description"]
    assert description.strip()
    assert "platzhalter" not in description.lower()


@pytest.mark.parametrize("tool", sorted(PLACEHOLDER_TOOLS))
def test_remaining_placeholders_are_still_described_as_such(rpc: Rpc, tool: str) -> None:
    """create_lead und log_activity kommen mit F08; bis dahin sagt die Beschreibung ehrlich Platzhalter."""
    assert "platzhalter" in _tools(rpc)[tool]["description"].lower()


@pytest.mark.parametrize("tool", sorted(READ_TOOLS))
def test_read_tools_do_not_answer_with_the_placeholder_error(rpc: Rpc, tool: str) -> None:
    """Ohne Datenbank kommt "nicht konfiguriert", nie "noch nicht implementiert" und nie Scheindaten."""
    arguments = {
        "search_products": {"query": "Gurtband"},
        "get_product": {"article_number": "FB-1001"},
        "find_customer": {"query": "firma.example"},
    }[tool]
    result = rpc("tools/call", {"name": tool, "arguments": arguments})["result"]
    assert result["isError"] is True
    assert NOT_IMPLEMENTED not in json.dumps(result, ensure_ascii=False)
    assert not result.get("structuredContent")


def test_search_products_input_schema(rpc: Rpc) -> None:
    schema = _tools(rpc)["search_products"]["inputSchema"]
    assert set(schema["properties"]) == {"query", "limit"}
    assert set(schema["required"]) == {"query"}, "limit ist optional (Standard 5)"
    assert "string" in _property_types(schema, "query")
    assert "integer" in _property_types(schema, "limit")


def test_get_product_input_schema(rpc: Rpc) -> None:
    schema = _tools(rpc)["get_product"]["inputSchema"]
    assert set(schema["properties"]) == {"article_number"}
    assert set(schema["required"]) == {"article_number"}
    assert "string" in _property_types(schema, "article_number")


def test_find_customer_input_schema(rpc: Rpc) -> None:
    schema = _tools(rpc)["find_customer"]["inputSchema"]
    assert set(schema["properties"]) == {"query"}
    assert set(schema["required"]) == {"query"}
    assert "string" in _property_types(schema, "query")


@pytest.mark.parametrize(
    ("tool", "fields"),
    [
        ("search_products", {"items", "count"}),
        ("get_product", {"product"}),
        ("find_customer", {"customer", "matched_by"}),
    ],
)
def test_read_tools_declare_an_output_schema(rpc: Rpc, tool: str, fields: set[str]) -> None:
    """Annahme: Das SDK leitet aus dem Rückgabetyp ein outputSchema ab. Etappe 4 belegt oder ändert das.

    Geprüft werden die festgelegten Feldnamen: Die Platzhalter (Rückgabe str) haben höchstens ein Schema
    mit dem Feld "result", das genügt hier nicht.
    """
    output_schema = _tools(rpc)[tool].get("outputSchema")
    assert isinstance(output_schema, dict) and output_schema.get("type") == "object"
    assert set(output_schema.get("properties", {})) == fields
