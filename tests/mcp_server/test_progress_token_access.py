"""Beleg fuer F10: Wie liest ein Werkzeug den progressToken des Clients im SDK (mcp 2.3.0), und was schickt das SDK zurueck?

Ohne Paketcode, mit einem eigenen Werkzeug des Tests (sync, `ctx: Context`, das immer eine Meldung versucht). Auf beiden Wegen (alt,
modern) schickt der Test Tokens verschiedenen Typs und haelt fest, was das Werkzeug ueber `ctx.request_context` sieht (`meta`, rohe
`params`) und welchen Token das SDK in der Meldung zurueckschickt. Die Tabelle steht in der Meldung und (mit `pytest -s`) auf der Ausgabe.

Belegt (jeweils per Assertion):
- Der Token, den das SDK zurueckschickt, ist der Wert aus den rohen `params["_meta"]["progressToken"]`, und zwar nur bei einer Zeichenkette
  oder einer Ganzzahl (`progress_token_from_params`). `bool` und Zahlen mit Nachkommastellen (auch 5.0) sind auf der Wire kein Token.
- `ctx.request_context.meta` ist ein Dict mit dem Schluessel `progress_token`; sein Wert gleicht bei str und int dem rohen Wert.
  `bool` kommt dort als `int` 1 an.
- Zahlen mit Nachkommastellen (1.2345678), Listen, Objekte und `null` lehnt das SDK vor dem Werkzeug ab (INVALID_PARAMS, feste Meldung; alter
  Weg: HTTP 200 mit JSON-RPC-Fehler, moderner Weg: HTTP 400): Das Werkzeug laeuft nicht, der Wert steht nicht in Antwort, Log, stdout
  oder stderr.
- 5.0 und `true` sind kein Token (das SDK schickt nichts zurueck), das Werkzeug laeuft, `meta` zeigt die Ganzzahl 5 bzw. 1.
"""

import logging
from typing import Any

import anyio.from_thread
import pytest
from mcp.server.mcpserver import Context
from mcp.shared.jsonrpc_dispatcher import progress_token_from_params
from mcp_testkit import BASE_URL, MCP_PATH, parse_sse_events, probe_app, tool_call_request
from starlette.testclient import TestClient

TOKENS: dict[str, Any] = {
    "str": "abc",
    "int": 7,
    "bool": True,
    "float5": 5.0,
    "float": 1.2345678,
    "list": ["GEHEIM-LISTE-4711"],
    "dict": {"GEHEIM-OBJEKT-4711": 1},
    "null": None,
}
# Was das SDK auf der Wire als Token erkennt (progress_token_from_params): nur str und int, nie bool
WIRE_TOKEN = {"str": "abc", "int": 7}
# Woran man den abgelehnten Wert in Antwort, Log, stdout und stderr erkennen wuerde
REJECTED_MARKERS = {"float": "1.2345678", "list": "GEHEIM-LISTE-4711", "dict": "GEHEIM-OBJEKT-4711"}
INVALID_PARAMS_CODE = -32602
INVALID_PARAMS_MESSAGE = "Invalid request parameters"


def _observe(era: str) -> dict[str, dict[str, Any]]:
    seen: dict[str, Any] = {}

    def tool(ctx: Context) -> str:
        context = ctx.request_context
        seen["meta"] = context.meta
        seen["meta_type"] = type(context.meta).__name__
        seen["params"] = context.params
        anyio.from_thread.run(
            ctx.report_progress, 1, 1, "x"
        )  # das SDK sendet nur, wenn es einen Token erkennt
        return "ok"

    table: dict[str, dict[str, Any]] = {}
    with TestClient(probe_app(tool), base_url=BASE_URL) as client:
        for name, value in TOKENS.items():
            seen.clear()
            payload, headers = tool_call_request(era, "probe_progress", {}, progress_token=value)
            if value is None:  # tool_call_request laesst None weg: das JSON-null selbst setzen
                params = payload["params"]
                params["_meta"] = {**params.get("_meta", {}), "progressToken": None}
            response = client.post(MCP_PATH, json=payload, headers=headers)
            text = response.text
            events = parse_sse_events(text) if "data:" in text else []
            body = (
                events[-1] if events else (response.json() if text.strip().startswith("{") else {})
            )
            raw_meta = seen["params"].get("_meta") if isinstance(seen.get("params"), dict) else None
            table[name] = {
                "status": response.status_code,
                "tool_ran": "meta" in seen,
                "is_error": bool(body.get("result", {}).get("isError"))
                if "result" in body
                else None,
                "rpc_error": body.get("error", {}).get("message") if "error" in body else None,
                "rpc_code": body.get("error", {}).get("code") if "error" in body else None,
                "text": text,
                "meta_type": seen.get("meta_type"),
                "meta": seen.get("meta"),
                "raw__meta": raw_meta,
                "echoed": [
                    e["params"]["progressToken"]
                    for e in events
                    if e.get("method") == "notifications/progress"
                ],
            }
    return table


@pytest.mark.parametrize(
    ("name", "expected"),
    [(name, WIRE_TOKEN.get(name)) for name in TOKENS],
)
def test_the_sdk_recognises_only_a_string_or_an_int_as_a_token_on_the_wire(
    name: str, expected: Any
) -> None:
    """`bool` und Zahlen mit Nachkommastellen (auch 5.0), Listen und Objekte sind kein Token (Funktion des SDKs)."""
    token = progress_token_from_params({"_meta": {"progressToken": TOKENS[name]}})
    assert token == expected
    assert type(token) is type(expected)


@pytest.mark.parametrize("era", ["alt", "modern"])
def test_the_progress_token_is_readable_from_the_request_context(era: str) -> None:
    table = _observe(era)
    report = "\n".join(f"  {name}: {row}" for name, row in table.items())
    print(f"Token-Zugriff ({era}):\n{report}")
    for name, value in (("str", "abc"), ("int", 7)):
        row = table[name]
        assert row["status"] == 200 and row["tool_ran"] is True, f"{name}: {row}\n{report}"
        assert row["raw__meta"]["progressToken"] == value, f"{name}: {row}\n{report}"
        meta = row["meta"]
        assert isinstance(meta, dict), f"{name}: meta ist kein Dict\n{report}"
        assert "progress_token" in meta, (
            f"{name}: der Schluessel progress_token fehlt in meta\n{report}"
        )
        assert meta["progress_token"] == row["raw__meta"]["progressToken"], f"{name}\n{report}"
        assert row["echoed"] == [value], (
            f"{name}: das SDK schickt nicht den rohen Token zurueck\n{report}"
        )


@pytest.mark.parametrize("era", ["alt", "modern"])
def test_a_bool_arrives_as_the_int_one_but_the_sdk_sends_nothing_back(era: str) -> None:
    table = _observe(era)
    report = "\n".join(f"  {name}: {row}" for name, row in table.items())
    row = table["bool"]
    assert row["status"] == 200 and row["tool_ran"] is True, f"{row}\n{report}"
    assert row["meta"]["progress_token"] == 1, f"{row}\n{report}"
    assert type(row["meta"]["progress_token"]) is int, f"{row}\n{report}"
    assert row["raw__meta"]["progressToken"] is True, f"{row}\n{report}"
    assert row["echoed"] == [], f"{row}\n{report}"


@pytest.mark.parametrize("era", ["alt", "modern"])
def test_a_whole_float_runs_the_tool_arrives_as_the_int_five_and_is_no_token_for_the_sdk(
    era: str,
) -> None:
    """5.0: Das Werkzeug laeuft, meta zeigt die Ganzzahl 5, das SDK schickt nichts zurueck (kein Token)."""
    table = _observe(era)
    report = "\n".join(f"  {name}: {row}" for name, row in table.items())
    row = table["float5"]
    assert row["status"] == 200 and row["tool_ran"] is True, f"{row}\n{report}"
    assert row["raw__meta"]["progressToken"] == 5.0, f"{row}\n{report}"
    assert row["meta"]["progress_token"] == 5, f"{row}\n{report}"
    assert type(row["meta"]["progress_token"]) is int, f"{row}\n{report}"
    assert row["echoed"] == [], f"{row}\n{report}"


@pytest.mark.parametrize("era", ["alt", "modern"])
@pytest.mark.parametrize("name", ["float", "list", "dict", "null"])
def test_the_sdk_rejects_a_float_a_list_an_object_and_null_before_the_tool(
    era: str,
    name: str,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Alter Weg: HTTP 200 mit JSON-RPC-Fehler, moderner Weg: HTTP 400; beide mit INVALID_PARAMS und fester Meldung.

    Das Werkzeug laeuft nicht, der abgelehnte Wert steht weder in der Antwort noch im Log (Root-Logger, alle Level) noch in stdout
    oder stderr.
    """
    caplog.set_level(logging.DEBUG)
    table = _observe(era)
    captured = capfd.readouterr()
    report = "\n".join(f"  {key}: {row}" for key, row in table.items())
    row = table[name]
    assert row["tool_ran"] is False, f"{name}: {row}\n{report}"
    assert row["echoed"] == [], f"{name}: {row}\n{report}"
    assert row["status"] == (200 if era == "alt" else 400), f"{name}: {row}\n{report}"
    assert row["rpc_code"] == INVALID_PARAMS_CODE, f"{name}: {row}\n{report}"
    assert row["rpc_error"] == INVALID_PARAMS_MESSAGE, f"{name}: {row}\n{report}"
    marker = REJECTED_MARKERS.get(
        name
    )  # null hat keinen eigenen Wert, an dem man ihn erkennen koennte
    if marker is not None:
        assert marker not in row["text"], f"der abgelehnte Wert steht in der Antwort\n{report}"
        for channel, text in (
            ("Log", caplog.text),
            ("stdout", captured.out),
            ("stderr", captured.err),
        ):
            assert marker not in text, f"der abgelehnte Wert steht in {channel}\n{report}"
