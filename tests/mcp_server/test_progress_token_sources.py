"""Tests der Quelle und der Zeichen des progressToken (F10, ADR 0007).

1. Quelle (M1): Das SDK schickt in jeder Meldung den Wert aus den rohen `params["_meta"]["progressToken"]` zurueck. Ein zweiter
   Schluessel `progress_token` (Snake-Case) in `_meta` ist fuer das SDK kein Token, landet aber in `ctx.request_context.meta` unter
   demselben Schluessel wie der Token. Der Datenservice darf sich deshalb nicht auf `meta` verlassen: Er prueft den Wert, den das SDK
   zuruecksendet. Die Tests der SDK-Tatsachen (probe_app) und die Tests der Werkzeuge stehen nebeneinander.
2. Zeichen: Ein gueltiger Token mit genau 64 Zeichen aus ungewoehnlichen Zeichen (ausserhalb der Grundebene, Steuerzeichen, Anfuehrungs-
   zeichen und Rueckstriche, U+2028/U+2029/U+0085/DEL, weitere Steuerzeichen) und ein einzelnes Ersatzzeichen muessen auf beiden Wegen
   streng bestehen: Status 200, genau 3 Meldungen mit dem Token unveraendert, normales Ergebnis, ein zweiter Aufruf wird beantwortet,
   weder der Wert noch repr, ascii, JSON oder ein Ausschnitt stehen in Log (ab INFO), stdout oder stderr. Jedes Ereignis mit dem
   groessten Token hat hoechstens 1 KiB (roh, mit Rahmen). Die Messwerte stehen (mit `pytest -s`) auf der Ausgabe.
3. Log: Fuer einen ungueltigen Token (65 Zeichen, 200 KiB) fangen die Tests alle Logger bei DEBUG sowie stdout und stderr ab: kein Zeichen
   des Tokens darf vorkommen.

Sonderzeichen stehen als \\U-Escapes.
"""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import anyio.from_thread
import pytest
from mcp.server.mcpserver import Context
from mcp_testkit import (
    BASE_URL,
    MCP_PATH,
    ScriptedDatabase,
    customer_handler,
    parse_sse_events,
    probe_app,
    progress_events,
    sse_progress_event_sizes,
    tool_call_request,
)
from starlette.testclient import TestClient

FIND_ARGUMENTS = {"query": "erika@muster.example"}
ERAS = ["alt", "modern"]
UNIQUE = "GEHEIM-4711-"
BIG = (UNIQUE * 20000)[: 200 * 1024]
SMALL_INVALID = (UNIQUE * 6)[:65]


@contextmanager
def _client(env: dict[str, str]) -> Iterator[TestClient]:
    from hoffmann_data.config import load_config
    from hoffmann_data.server import create_app

    app = create_app(load_config(env), database=ScriptedDatabase(customer_handler, []))
    with TestClient(app, base_url=BASE_URL) as client:
        yield client


def _meta_request(
    era: str,
    items: list[tuple[str, Any]],
    authorization: str | None = None,
    tool: str = "find_customer",
    arguments: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], dict[str, str]]:
    """tools/call mit genau diesen `_meta`-Schluesseln in dieser Reihenfolge (nach den Umschlagfeldern des modernen Wegs)."""
    payload, headers = tool_call_request(
        era, tool, FIND_ARGUMENTS if arguments is None else arguments, authorization=authorization
    )
    params = payload["params"]
    params["_meta"] = {**params.get("_meta", {}), **dict(items)}
    return payload, headers


def _echoed(response: Any) -> list[Any]:
    text = response.text
    events = parse_sse_events(text) if "data:" in text else []
    return [e["params"]["progressToken"] for e in progress_events(events)]


def _final(response: Any) -> dict[str, Any]:
    text = response.text
    if "data:" in text:
        return parse_sse_events(text)[-1]  # type: ignore[no-any-return]
    return response.json()  # type: ignore[no-any-return]


# Quelle des Tokens (M1)

CAMEL_BIG_SNAKE_OK = [("progressToken", BIG), ("progress_token", "ok")]
CAMEL_OK_SNAKE_BIG = [("progressToken", "ok"), ("progress_token", BIG)]
SOURCE_CASES = [
    # (Schluessel in dieser Reihenfolge, was das SDK zuruecksendet, was unser Werkzeug zuruecksendet)
    pytest.param(CAMEL_BIG_SNAKE_OK, [BIG], [], id="a1-camel-gross-dann-snake-ok"),
    pytest.param(CAMEL_BIG_SNAKE_OK[::-1], [BIG], [], id="a2-snake-ok-dann-camel-gross"),
    pytest.param(CAMEL_OK_SNAKE_BIG, ["ok"], ["ok"] * 3, id="b1-camel-ok-dann-snake-gross"),
    pytest.param(CAMEL_OK_SNAKE_BIG[::-1], ["ok"], ["ok"] * 3, id="b2-snake-gross-dann-camel-ok"),
    pytest.param([("progress_token", "ok")], [], [], id="c-nur-snake"),
]


@pytest.mark.parametrize("era", ERAS)
@pytest.mark.parametrize(("items", "sdk_echo", "ours_echo"), SOURCE_CASES)
def test_the_sdk_sends_back_only_the_camel_case_wire_key(
    era: str, items: list[tuple[str, Any]], sdk_echo: list[Any], ours_echo: list[Any]
) -> None:
    """SDK-Tatsache, gemessen mit einem Werkzeug, das immer eine Meldung versucht: Zurueck kommt der Wert von `progressToken`."""

    def tool(ctx: Context) -> str:
        anyio.from_thread.run(ctx.report_progress, 1, 1, "x")
        return "ok"

    payload, headers = _meta_request(era, items, tool="probe_progress", arguments={})
    with TestClient(probe_app(tool), base_url=BASE_URL) as client:
        response = client.post(MCP_PATH, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    assert _echoed(response) == sdk_echo


@pytest.mark.parametrize("era", ERAS)
@pytest.mark.parametrize(("items", "sdk_echo", "ours_echo"), SOURCE_CASES)
def test_the_tool_decides_by_the_token_the_sdk_sends_back(
    write_env: dict[str, str],
    token: str,
    era: str,
    items: list[tuple[str, Any]],
    sdk_echo: list[Any],
    ours_echo: list[Any],
) -> None:
    """Ein riesiger `progressToken` kommt nie zurueck, auch nicht mit einem kurzen `progress_token` daneben (M1);
    ein kurzer `progressToken` meldet, auch mit einem riesigen `progress_token` daneben; nur `progress_token` meldet nichts."""
    payload, headers = _meta_request(era, items, authorization=token)
    with _client(write_env) as client:
        response = client.post(MCP_PATH, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    assert (UNIQUE * 3) not in response.text, "der grosse Token kam in die Antwort zurueck"
    assert _echoed(response) == ours_echo
    assert _final(response)["result"].get("isError") is not True


# Ungewoehnliche Zeichen in einem gueltigen Token (genau 64 Zeichen, bzw. 1 Zeichen)

CHAR_TOKENS = [
    pytest.param("\U0001f600" * 64, id="nicht-bmp"),
    pytest.param("\U00000000\U00000001\U0000000a\U0000000d" * 16, id="steuerzeichen"),
    pytest.param('"\\' * 32, id="anfuehrungszeichen-und-rueckstriche"),
    pytest.param("\U00002028\U00002029\U00000085\U0000007f" * 16, id="zeilentrenner-und-del"),
    pytest.param(
        ("\U0000000b\U0000000c\U0000001c\U0000001d\U0000001e" * 13)[:64],
        id="weitere-steuerzeichen",
    ),
    # Schlimmster Fall der Steuerzeichen: je Zeichen ein 6-Byte-Escape (\u0001) im JSON
    pytest.param("\U00000001" * 64, id="worst-case-u0001"),
]
LONE_SURROGATE = "\U0000d800"
# Die Faelle mit dem groessten Token auf der Wire: Nicht-BMP (4 Bytes roh, 12 Bytes als Escape-Paar), Steuerzeichen (je 2 bis 6 Bytes),
# und der Worst Case aus 64 mal U+0001 (je 6 Bytes)
LARGEST_TOKENS = [CHAR_TOKENS[0], CHAR_TOKENS[1], CHAR_TOKENS[-1]]
MAX_EVENT_BYTES = 1024


def _probes(value: str) -> set[str]:
    """Zeichenfolgen, die nirgends im Log, in stdout oder in stderr stehen duerfen (Wert, repr, ascii, JSON, Ausschnitte)."""
    head = value[:8]
    probes = {
        value,
        repr(value),
        ascii(value),
        json.dumps(value),
        json.dumps(value)[1:-1],
        json.dumps(value, ensure_ascii=False),
        head,
        repr(head)[1:-1],
        ascii(head)[1:-1],
        json.dumps(head)[1:-1],
    }
    if "\U00000000" in value:
        probes.add("\\u0000")
    if value == LONE_SURROGATE:
        probes.add("\\ud800")
    return {probe for probe in probes if len(probe) >= 6 or probe == LONE_SURROGATE}


def _round_trip(
    write_env: dict[str, str],
    token: str,
    value: str,
    era: str,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> Any:
    """Ein Aufruf mit dem Token `value` und ein zweiter ohne Token; prueft streng, gibt die erste Antwort zurueck.

    Log (Root-Logger ab INFO), stdout und stderr duerfen den Wert nicht enthalten. Bei DEBUG schreibt der Transport (sse_starlette) die
    ganze Meldung ins Log (dokumentierte Ausnahme), darum gilt die Pruefung ab INFO.
    """
    caplog.set_level(logging.INFO)
    payload, headers = tool_call_request(
        era, "find_customer", FIND_ARGUMENTS, progress_token=value, authorization=token
    )
    body = json.dumps(payload).encode(
        "ascii"
    )  # ASCII mit \\u-Escapes: auch ein einzelnes Ersatzzeichen laesst sich so senden
    plain_payload, plain_headers = tool_call_request(
        era, "find_customer", FIND_ARGUMENTS, authorization=token
    )
    with _client(write_env) as client:
        response = client.post(MCP_PATH, content=body, headers=headers)
        second = client.post(MCP_PATH, json=plain_payload, headers=plain_headers)
    captured = capfd.readouterr()
    echoed = _echoed(response)
    print(
        f"Zeichen-Token ({era}, {len(value)} Zeichen): status={response.status_code}, "
        f"content-type={response.headers.get('content-type')}, meldungen={len(echoed)}, "
        f"zurueck gleich gesendet={all(item == value for item in echoed)}, "
        f"zweiter Aufruf={second.status_code}, "
        f"log-zeilen={[(r.name, r.levelname) for r in caplog.records]}, "
        f"stdout/stderr leer={not captured.out and not captured.err}"
    )
    assert response.status_code == 200, ascii(response.text[:300])
    assert len(echoed) == 3, (
        f"erwartet 3 Meldungen, es kamen {len(echoed)}: {response.text[:300]!a}"
    )
    assert all(item == value for item in echoed), "der Token kam veraendert zurueck"
    assert _final(response)["result"].get("isError") is not True
    assert second.status_code == 200, ascii(second.text[:300])
    assert _final(second)["result"].get("isError") is not True
    for name, text in (("Log", caplog.text), ("stdout", captured.out), ("stderr", captured.err)):
        for probe in _probes(value):
            assert probe not in text, f"Zeichen des Tokens stehen in {name}: {ascii(probe)[:60]}"
    return response


@pytest.mark.parametrize("era", ERAS)
@pytest.mark.parametrize("value", CHAR_TOKENS)
def test_a_valid_token_with_unusual_characters_is_returned_unchanged_and_stays_out_of_the_log(
    write_env: dict[str, str],
    token: str,
    value: str,
    era: str,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    _round_trip(write_env, token, value, era, caplog, capfd)


def test_the_modern_era_returns_a_lone_surrogate_token_unchanged_and_stays_out_of_the_log(
    write_env: dict[str, str],
    token: str,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Moderner Weg (gemessen): HTTP 200, 3 Meldungen, der Token kommt unveraendert zurueck, kein Leck."""
    _round_trip(write_env, token, LONE_SURROGATE, "modern", caplog, capfd)


def test_the_old_era_rejects_a_lone_surrogate_while_parsing_and_keeps_running(
    write_env: dict[str, str],
    token: str,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Alter Weg (gemessen, Verhalten des SDKs, kein Fehler des Dienstes): Der JSON-Parser des SDKs (jiter) lehnt einen unpaarigen
    Surrogat-Escape beim Parsen ab. Antwort: HTTP 400, JSON-RPC-Fehler -32700, Text beginnt mit "Parse error", id null. Das Werkzeug
    laeuft nicht (keine Meldung), der Wert steht weder in der Antwort noch im Log (Root, ab INFO) noch in stdout oder stderr, und ein
    zweiter Aufruf ohne Token antwortet mit 200."""
    caplog.set_level(logging.INFO)
    payload, headers = tool_call_request(
        "alt", "find_customer", FIND_ARGUMENTS, progress_token=LONE_SURROGATE, authorization=token
    )
    plain_payload, plain_headers = tool_call_request(
        "alt", "find_customer", FIND_ARGUMENTS, authorization=token
    )
    body = json.dumps(payload).encode("ascii")
    with _client(write_env) as client:
        response = client.post(MCP_PATH, content=body, headers=headers)
        second = client.post(MCP_PATH, json=plain_payload, headers=plain_headers)
    captured = capfd.readouterr()
    assert response.status_code == 400, f"{response.text[:300]!a}"
    error = response.json()
    assert error["id"] is None
    assert error["error"]["code"] == -32700
    assert error["error"]["message"].startswith("Parse error")
    assert _echoed(response) == []
    for needle in ("\\ud800", "ud800", LONE_SURROGATE):
        assert needle not in response.text, "der Wert steht in der Antwort"
        for channel, text in (
            ("Log", caplog.text),
            ("stdout", captured.out),
            ("stderr", captured.err),
        ):
            assert needle not in text, f"der Wert steht in {channel}"
    assert not captured.out and not captured.err
    assert second.status_code == 200, f"{second.text[:300]!a}"
    assert _final(second)["result"].get("isError") is not True


@pytest.mark.parametrize("era", ERAS)
@pytest.mark.parametrize("value", LARGEST_TOKENS)
def test_every_progress_event_stays_below_one_kib(
    write_env: dict[str, str],
    token: str,
    value: str,
    era: str,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    """Rohe Groesse jedes SSE-Ereignisses (Rahmen und Endzeile eingeschlossen) bei dem groessten Token (64 Zeichen).

    Der Ausdruck nennt je Klasse die Laenge des Tokens als JSON (ASCII-Escapes bzw. roh in UTF-8), damit sichtbar ist, was gemessen wird.
    """
    response = _round_trip(write_env, token, value, era, caplog, capfd)
    sizes = sse_progress_event_sizes(response.content)
    ascii_json = len(json.dumps(value))
    utf8_json = len(json.dumps(value, ensure_ascii=False).encode())
    print(
        f"Bytegroesse je Ereignis ({era}, {len(value)} Zeichen, Token als JSON: {ascii_json} Bytes mit Escapes, "
        f"{utf8_json} Bytes als rohes UTF-8): {sizes}, Maximum {max(sizes, default=0)}"
    )
    assert len(sizes) == 3
    assert max(sizes) <= MAX_EVENT_BYTES


# Ungueltiger Token: kein Zeichen in Log, stdout oder stderr (alle Logger, alle Level)


@pytest.mark.parametrize("era", ERAS)
@pytest.mark.parametrize(
    "value", [pytest.param(SMALL_INVALID, id="65-zeichen"), pytest.param(BIG, id="200-kib")]
)
def test_an_invalid_token_appears_in_no_log_line_and_not_in_stdout_or_stderr(
    write_env: dict[str, str],
    token: str,
    value: str,
    era: str,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)  # Root-Logger: alle Logger, alle Level
    payload, headers = tool_call_request(
        era, "find_customer", FIND_ARGUMENTS, progress_token=value, authorization=token
    )
    with _client(write_env) as client:
        response = client.post(MCP_PATH, json=payload, headers=headers)
    captured = capfd.readouterr()
    assert response.status_code == 200, response.text[:300]
    assert _echoed(response) == []
    needle = UNIQUE * 3
    for name, text in (("Log", caplog.text), ("stdout", captured.out), ("stderr", captured.err)):
        assert needle not in text, f"Zeichen des Tokens stehen in {name}"
        assert value[:65] not in text, f"Zeichen des Tokens stehen in {name}"


# Eine Ganzzahl ueber 64 Bit (null gehoert zu den abgelehnten Werten, siehe test_progress_token_access.py)


@pytest.mark.parametrize("era", ERAS)
def test_a_huge_integer_token_runs_the_tool_and_gets_no_message(
    write_env: dict[str, str], token: str, era: str
) -> None:
    """Gemessen: Eine Ganzzahl ueber 64 Bit (2**64 + 1) laeuft auf beiden Wegen normal; sie liegt ueber MAX_TOKEN_INT, also keine Meldung."""
    payload, headers = _meta_request(era, [("progressToken", 2**64 + 1)], authorization=token)
    with _client(write_env) as client:
        response = client.post(MCP_PATH, json=payload, headers=headers)
    assert response.status_code == 200, f"{response.text[:300]!a}"
    assert _echoed(response) == []
    assert _final(response)["result"].get("isError") is not True


# Anfragegroesse: gemessen, 413 auf beiden Wegen


@pytest.mark.parametrize("era", ERAS)
@pytest.mark.parametrize("where", ["token", "argument"])
def test_a_300_kib_request_is_rejected_with_413_on_both_eras_and_the_server_keeps_running(
    write_env: dict[str, str], token: str, era: str, where: str
) -> None:
    """Eine Anfrage mit 300 KiB (als Token oder als Argument) wird auf beiden Wegen mit HTTP 413 abgewiesen (Grenze 256 KiB, `MAX_REQUEST_BODY_BYTES`);
    ein zweiter normaler Aufruf liefert 200."""
    big = "x" * (300 * 1024)
    if where == "token":
        payload, headers = tool_call_request(
            era, "find_customer", FIND_ARGUMENTS, progress_token=big, authorization=token
        )
    else:
        payload, headers = tool_call_request(
            era, "find_customer", {"query": big}, authorization=token
        )
    plain_payload, plain_headers = tool_call_request(
        era, "find_customer", FIND_ARGUMENTS, authorization=token
    )
    with _client(write_env) as client:
        response = client.post(MCP_PATH, json=payload, headers=headers)
        again = client.post(MCP_PATH, json=plain_payload, headers=plain_headers)
    assert response.status_code == 413, f"{response.text[:200]!a}"
    assert "Request body too large" in response.text
    assert again.status_code == 200, f"{again.text[:300]!a}"
    assert _final(again)["result"].get("isError") is not True
