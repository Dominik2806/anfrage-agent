"""Tests der acht Schnittstellen (F06) über HTTP mit gültigem Token, mit rohem JSON-RPC.

Der Weg geht durch Middleware, SDK-App und Transport. mcp.Client in-process würde die Auth umgehen.
Alle Platzhalter sind parameterlos (die Signaturen kommen mit den echten Werkzeugen ab F07),
deshalb genügen leere Argumente.
"""

import json
from collections.abc import Callable
from typing import Any

import pytest
from mcp_testkit import NOT_IMPLEMENTED, PROMPT_NAMES, RESOURCE_URIS, TOOL_NAMES

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


@pytest.mark.parametrize("tool", sorted(TOOL_NAMES))
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
    assert NOT_IMPLEMENTED in body["error"]["message"]


def test_prompt_placeholder_returns_an_error_not_data(rpc: Rpc) -> None:
    """Der Prompt-Fehler läuft im SDK über ValueError(str(e)). Die Meldung muss beim Client ankommen."""
    body = rpc("prompts/get", {"name": "antwort_entwurf", "arguments": {}})
    assert "error" in body and "result" not in body
    assert NOT_IMPLEMENTED in json.dumps(body, ensure_ascii=False)


def test_unknown_tool_is_an_error(rpc: Rpc) -> None:
    body = rpc("tools/call", {"name": "send_email", "arguments": {}})
    assert body.get("result", {}).get("isError") is True or "error" in body


def test_error_text_does_not_echo_arguments(rpc: Rpc) -> None:
    """Der Platzhalterfehler gibt keine Eingabewerte aus (Kundentext ist Daten, kein Echo)."""
    body = rpc("tools/call", {"name": "search_products", "arguments": {"query": "GEHEIM-4711"}})
    assert "GEHEIM-4711" not in json.dumps(body, ensure_ascii=False)
