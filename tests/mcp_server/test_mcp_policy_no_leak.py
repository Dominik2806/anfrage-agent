"""Tests: Eingabewerte kommen in keiner Meldung, keinem Log und keiner Ausgabe der Resources und des Prompts vor (F09, ADR 0006).

Eigene Datei, nicht test_mcp_no_leak.py (Regeltest). Wie dort und in test_mcp_write_no_leak.py dient SECRET_MARKER als
Eingabewert, der nirgends zurückkommen darf: nicht in der Antwort, nicht im Log (DEBUG, alle Logger), nicht auf stdout
und stderr.

Die Fehler des Prompts kommen aus der Unterklasse DraftPrompt (draft_prompt.py, ADR 0006): Sie überschreibt render und wirft
MCPError(INVALID_PARAMS, feste Meldung). Das SDK reicht einen MCPError unverändert durch und loggt ihn nicht. Dagegen macht
es aus jeder anderen Ausnahme in render ein ValueError und loggt es mit der ganzen Ursachenkette auf ERROR (mcp
jsonrpc_dispatcher.py, _handle_request; server.py, get_prompt). Eine Pydantic-Meldung (validate_call, fehlendes Pflichtargument
oder unbekanntes Argument des SDK) enthielte dort die Eingabewerte. Deshalb prüfen die Fehlertests mehr als "Marker nicht im
Log": Im Log (alle Logger, DEBUG) darf kein einziger Eintrag mit Level ERROR oder höher und keiner mit exc_info stehen.

Gültige Aufrufe: Bei DEBUG loggt der Transport die ganze Antwort mit (Logger sse_starlette.sse, Meldung "chunk: ..."). Sie
enthält den Kundentext in Nachricht 2 absichtlich. Die Tests mit gültigen Aufrufen nehmen nur diese Einträge aus (_logged,
without_response_chunks); alle übrigen Einträge, stdout und stderr bleiben vollständig geprüft. Bei Fehlerfällen wird ohne
Ausnahme alles geprüft, weil die Antwort dort keinen Wert enthält. Folge für den Betrieb: Bei DEBUG auf sse_starlette steht jede
Antwort im Log, DEBUG nie mit echten Daten (ADR 0006).

Nicht-Text-Argumente (Liste, Zahl) weist schon das Protokoll ab (arguments: dict[str, str]): Code -32602, kein Wert in der
Antwort, kein Log. Das belegt test_a_non_text_argument_is_rejected_without_echo.

Bekannte Grenze (Issue #37, gleiche Klasse wie Issue #21, mcp-server/CLAUDE.md), zwei Fälle als xfail(strict=True) festgehalten: Das SDK spiegelt den Wert
selbst, bevor Code des Datenservice läuft. (1) Die unbekannte URI: Meldung "Unknown resource: ...", data.uri und ein
INFO-Eintrag im Log (server.py, _handle_read_resource). (2) Der unbekannte Prompt-Name: Meldung "Unknown prompt: ..." und ein
ERROR-Eintrag mit Stacktrace im Log (server.py, get_prompt; Dispatcher). Ein SDK, das den Wert nicht mehr spiegelt, lässt sie
fehlschlagen (XPASS) und damit auffallen.
"""

import json
import logging
from collections.abc import Callable
from typing import Any

import pytest
from mcp_testkit import MCP_PATH, RPC_HEADERS, SECRET_MARKER, parse_rpc_body
from starlette.testclient import TestClient

Rpc = Callable[..., dict[str, Any]]

PROMPT = "antwort_entwurf"
GOOD = {"sprache": "de", "anfrage": "Bitte um ein Angebot."}
INVALID_PARAMS_CODE = -32602
# ASCII-sicher: keine literalen Umlaute im Text der Begruendung
SDK_ECHO = (
    "Bekannte Grenze (Issue #37, gleiche Klasse wie #21): Das SDK spiegelt den Wert in seiner eigenen Meldung und im Log, "
    "bevor Code des Datenservice laeuft. Nicht aenderbar ohne SDK-Eingriff, siehe ADR 0006."
)


def _logged(caplog: pytest.LogCaptureFixture, *, without_response_chunks: bool = False) -> str:
    """Der Text aller Log-Einträge (alle Logger, mit Stacktrace-Text).

    without_response_chunks: lässt die Einträge weg, in denen der Transport bei DEBUG die Antwort selbst mitloggt
    (Logger sse_starlette.sse, Meldung beginnt mit "chunk:"). Nur für gültige Aufrufe, deren Antwort den Wert absichtlich
    enthält (Nachricht 2 des Prompts); unser Code schreibt dort nichts ins Log. Alle anderen Einträge bleiben geprüft.
    """
    records = caplog.records
    if without_response_chunks:
        records = [
            r
            for r in records
            if not (r.name == "sse_starlette.sse" and r.getMessage().startswith("chunk:"))
        ]
    return "\n".join(f"{r.getMessage()} {r.exc_text or ''}" for r in records)


def _everything(
    body: Any, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> str:
    """Antwort, stdout, stderr und das ganze Log, ohne Ausnahme (für Fehlerfälle: die Antwort enthält keinen Wert)."""
    captured = capsys.readouterr()
    dumped = body if isinstance(body, str) else json.dumps(body, ensure_ascii=False)
    return dumped + captured.out + captured.err + _logged(caplog)


def _assert_quiet_log(caplog: pytest.LogCaptureFixture) -> None:
    """Kein Eintrag mit Level ERROR oder höher und keiner mit exc_info oder Stacktrace-Text (alle Logger, DEBUG)."""
    for record in caplog.records:
        assert record.levelno < logging.ERROR, f"{record.name}: Level {record.levelname}"
        assert record.exc_info is None, f"{record.name}: exc_info"
        assert not record.exc_text, f"{record.name}: Stacktrace-Text"


def _limits() -> Any:
    from hoffmann_data import limits

    return limits


def _assert_fixed_error(body: dict[str, Any], message: str) -> None:
    """Genau die feste Meldung, Code -32602, unverändert (kein SDK-Text, kein data), kein Ergebnis."""
    assert "result" not in body, body
    error = body["error"]
    assert error["code"] == INVALID_PARAMS_CODE
    assert error["message"] == message
    assert SECRET_MARKER not in json.dumps(error, ensure_ascii=False)
    assert not error.get("data")


# Meldungen


def test_all_fixed_messages_are_distinct_and_free_of_values() -> None:
    """Prüft die neuen Meldungen von F09 und alle früheren *_INVALID-Meldungen sowie drei Meldungen von F08."""
    from hoffmann_data import limits, policies

    messages: dict[str, str] = {
        "policies.POLICY_UNAVAILABLE": policies.POLICY_UNAVAILABLE,
        "limits.INQUIRY_INVALID": limits.INQUIRY_INVALID,
        "limits.CUSTOMER_INFO_INVALID": limits.CUSTOMER_INFO_INVALID,
        "limits.FACTS_INVALID": limits.FACTS_INVALID,
        "limits.PROMPT_ARGUMENTS_UNKNOWN": limits.PROMPT_ARGUMENTS_UNKNOWN,
    }
    for name, value in vars(limits).items():
        if name.endswith("_INVALID") and isinstance(value, str):
            messages[f"limits.{name}"] = value
    for name in ("CUSTOMER_NOT_FOUND", "CONTACT_NOT_FOUND", "LEAD_CONFLICT"):
        messages[f"limits.{name}"] = getattr(limits, name)
    assert all(isinstance(v, str) and v.strip() for v in messages.values())
    values = list(messages.values())
    assert len(set(values)) == len(values), "zwei Meldungen sind gleich"
    for name, value in messages.items():
        assert SECRET_MARKER not in value, name
        for other_name, other in messages.items():
            if name != other_name:
                assert value not in other, f"{name} steckt in {other_name}"


# Ungültige Werte: feste Meldung je Argument, unverändert, kein Wert, kein Log mit ERROR

# (Fall, Argumente, Name der festen Meldung in limits)
INVALID_CALLS = [
    ("sprache", {"sprache": SECRET_MARKER, "anfrage": "Bitte um ein Angebot."}, "LANGUAGE_INVALID"),
    (
        "anfrage-steuerzeichen",
        {"sprache": "de", "anfrage": SECRET_MARKER + "\x00"},
        "INQUIRY_INVALID",
    ),
    (
        "anfrage-zu-lang",
        {"sprache": "de", "anfrage": (SECRET_MARKER + " ") * 600},
        "INQUIRY_INVALID",
    ),
    ("kunde-zu-lang", {**GOOD, "kunde": (SECRET_MARKER + " ") * 200}, "CUSTOMER_INFO_INVALID"),
    (
        "angaben-zu-lang",
        {**GOOD, "belegte_angaben": (SECRET_MARKER + " ") * 400},
        "FACTS_INVALID",
    ),
    ("kunde-steuerzeichen", {**GOOD, "kunde": SECRET_MARKER + "\x1b"}, "CUSTOMER_INFO_INVALID"),
]


@pytest.mark.parametrize(
    ("arguments", "name"),
    [case[1:] for case in INVALID_CALLS],
    ids=[case[0] for case in INVALID_CALLS],
)
def test_an_invalid_argument_gives_the_fixed_message_and_no_error_log(
    rpc: Rpc,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    arguments: dict[str, str],
    name: str,
) -> None:
    caplog.set_level(logging.DEBUG)
    body = rpc("prompts/get", {"name": PROMPT, "arguments": arguments})
    _assert_fixed_error(body, getattr(_limits(), name))
    _assert_quiet_log(caplog)
    assert SECRET_MARKER not in _everything(body, caplog, capsys)


# Fehlende Pflichtargumente: dieselbe feste Meldung wie bei ungültigem Wert (a)


def test_a_missing_sprache_gives_the_fixed_message_without_the_other_values(
    rpc: Rpc, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.DEBUG)
    body = rpc(
        "prompts/get",
        {"name": PROMPT, "arguments": {"anfrage": "Bitte um ein Angebot.", "kunde": SECRET_MARKER}},
    )
    _assert_fixed_error(body, _limits().LANGUAGE_INVALID)
    _assert_quiet_log(caplog)
    assert SECRET_MARKER not in _everything(body, caplog, capsys)


def test_a_missing_anfrage_gives_the_fixed_message_without_the_other_values(
    rpc: Rpc, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.DEBUG)
    body = rpc(
        "prompts/get",
        {"name": PROMPT, "arguments": {"sprache": "de", "belegte_angaben": SECRET_MARKER}},
    )
    _assert_fixed_error(body, _limits().INQUIRY_INVALID)
    _assert_quiet_log(caplog)
    assert SECRET_MARKER not in _everything(body, caplog, capsys)


def test_a_call_without_any_arguments_gives_the_fixed_message_and_no_error_log(
    rpc: Rpc, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    body = rpc("prompts/get", {"name": PROMPT})
    _assert_fixed_error(body, _limits().LANGUAGE_INVALID)
    _assert_quiet_log(caplog)


def test_an_empty_arguments_object_gives_the_same_message(
    rpc: Rpc, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    _assert_fixed_error(
        rpc("prompts/get", {"name": PROMPT, "arguments": {}}), _limits().LANGUAGE_INVALID
    )
    _assert_quiet_log(caplog)


# Unbekannte Argumente (b): Wert und Name erscheinen nirgends


def test_an_unknown_argument_with_the_marker_as_value_is_not_echoed(
    rpc: Rpc, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.DEBUG)
    body = rpc("prompts/get", {"name": PROMPT, "arguments": {**GOOD, "unbekannt": SECRET_MARKER}})
    _assert_fixed_error(body, _limits().PROMPT_ARGUMENTS_UNKNOWN)
    _assert_quiet_log(caplog)
    assert SECRET_MARKER not in _everything(body, caplog, capsys)


def test_an_unknown_argument_with_the_marker_as_name_is_not_echoed(
    rpc: Rpc, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.DEBUG)
    body = rpc("prompts/get", {"name": PROMPT, "arguments": {**GOOD, SECRET_MARKER: "x"}})
    _assert_fixed_error(body, _limits().PROMPT_ARGUMENTS_UNKNOWN)
    _assert_quiet_log(caplog)
    assert SECRET_MARKER not in _everything(body, caplog, capsys)


# Mehrere Fehler gleichzeitig: ein Fehler, feste Reihenfolge, kein Wert


def test_several_errors_give_exactly_one_fixed_message_in_a_fixed_order(
    rpc: Rpc, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """Reihenfolge: unbekannte Argumente, sprache, anfrage, kunde, belegte_angaben."""
    limits = _limits()
    caplog.set_level(logging.DEBUG)
    everything: list[Any] = []
    arguments: dict[str, str] = {
        "sprache": SECRET_MARKER,
        "anfrage": SECRET_MARKER + "\x00",
        "kunde": (SECRET_MARKER + " ") * 200,
        "belegte_angaben": (SECRET_MARKER + " ") * 400,
        f"{SECRET_MARKER}-unbekannt": SECRET_MARKER,
    }
    # Von Schritt zu Schritt wird der jeweils erste Fehler behoben (nicht das Argument entfernt: ein fehlendes
    # Pflichtargument ist selbst ein Fehler und käme wieder zuerst)
    steps = [(dict(arguments), limits.PROMPT_ARGUMENTS_UNKNOWN)]
    del arguments[f"{SECRET_MARKER}-unbekannt"]
    steps.append((dict(arguments), limits.LANGUAGE_INVALID))
    arguments["sprache"] = "de"
    steps.append((dict(arguments), limits.INQUIRY_INVALID))
    arguments["anfrage"] = "Bitte um ein Angebot."
    steps.append((dict(arguments), limits.CUSTOMER_INFO_INVALID))
    del arguments["kunde"]
    steps.append((dict(arguments), limits.FACTS_INVALID))
    for current, message in steps:
        body = rpc("prompts/get", {"name": PROMPT, "arguments": current})
        _assert_fixed_error(body, message)
        everything.append(body)
    _assert_quiet_log(caplog)
    assert SECRET_MARKER not in _everything(everything, caplog, capsys)


# Nicht-Text-Argumente weist das Protokoll ab (Befund 1)


@pytest.mark.parametrize(
    "value",
    [[SECRET_MARKER], 5, True, {"a": SECRET_MARKER}],
    ids=["liste", "zahl", "bool", "objekt"],
)
def test_a_non_text_argument_is_rejected_without_echo(
    client: TestClient,
    auth_headers: dict[str, str],
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    value: Any,
) -> None:
    """arguments ist dict[str, str]: Die Pydantic-Prüfung des SDK läuft vor jedem Handler, die Antwort trägt keinen Wert."""
    caplog.set_level(logging.DEBUG)
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "prompts/get",
        "params": {"name": PROMPT, "arguments": {**GOOD, "anfrage": value}},
    }
    response = client.post(MCP_PATH, json=payload, headers={**RPC_HEADERS, **auth_headers})
    # Genau HTTP 200 mit einem JSON-RPC-Fehler: Ein 401, 413 oder 421 hätte eine andere Ursache als das Argument
    assert response.status_code == 200
    body = parse_rpc_body(response)
    assert "result" not in body
    assert body["error"]["code"] == INVALID_PARAMS_CODE
    _assert_quiet_log(caplog)
    assert SECRET_MARKER not in _everything(response.text, caplog, capsys)


# Gültige Eingaben stehen nur im Datenteil


def test_a_valid_value_appears_only_in_the_data_message_and_never_in_logs(
    rpc: Rpc, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.DEBUG)
    arguments = {
        "sprache": "de",
        "anfrage": f"Anfrage {SECRET_MARKER}",
        "kunde": f"Kunde {SECRET_MARKER}",
        "belegte_angaben": f"Angaben {SECRET_MARKER}",
    }
    body = rpc("prompts/get", {"name": PROMPT, "arguments": arguments})
    result = body["result"]
    first = result["messages"][0]["content"]["text"]
    second = result["messages"][1]["content"]["text"]
    assert SECRET_MARKER not in first
    assert SECRET_MARKER not in str(result.get("description") or "")
    assert second.count(SECRET_MARKER) == 3
    _assert_quiet_log(caplog)
    # Die Antwort selbst steht bei DEBUG im Log des Transports (sse_starlette.sse, "chunk:"); sie enthält den Wert in
    # Nachricht 2 absichtlich und wird deshalb hier ausgenommen. Alles andere im Log, stdout und stderr bleibt geprüft.
    captured = capsys.readouterr()
    rest = json.dumps({k: v for k, v in result.items() if k != "messages"}, ensure_ascii=False)
    rest += captured.out + captured.err + _logged(caplog, without_response_chunks=True)
    assert SECRET_MARKER not in rest


def test_a_valid_call_prints_and_logs_no_value(
    rpc: Rpc, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """Unser Code schreibt keinen Wert ins Log; ausgenommen sind nur die Antwort-Chunks des Transports (siehe _logged)."""
    caplog.set_level(logging.DEBUG)
    rpc("prompts/get", {"name": PROMPT, "arguments": {**GOOD, "anfrage": SECRET_MARKER}})
    captured = capsys.readouterr()
    logged = _logged(caplog, without_response_chunks=True)
    for where, text in {"stdout": captured.out, "stderr": captured.err, "Log": logged}.items():
        assert SECRET_MARKER not in text, where


def test_an_empty_optional_argument_is_a_valid_call_without_error_log(
    rpc: Rpc, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    """Ein leeres optionales Argument heißt "fehlt": Kein Fehlerweg, kein Eintrag ab ERROR, kein Wert in Log oder Ausgabe."""
    caplog.set_level(logging.DEBUG)
    body = rpc(
        "prompts/get",
        {
            "name": PROMPT,
            "arguments": {
                "sprache": "de",
                "anfrage": f"Bitte {SECRET_MARKER} anbieten",
                "kunde": "   ",
                "belegte_angaben": "",
            },
        },
    )
    assert "error" not in body, body
    first = body["result"]["messages"][0]["content"]["text"]
    second = body["result"]["messages"][1]["content"]["text"]
    assert SECRET_MARKER not in first
    assert second.count(SECRET_MARKER) == 1
    _assert_quiet_log(caplog)
    captured = capsys.readouterr()
    logged = _logged(caplog, without_response_chunks=True)
    for where, text in {"stdout": captured.out, "stderr": captured.err, "Log": logged}.items():
        assert SECRET_MARKER not in text, where


# Bekannte Grenze des SDK (xfail, strict)


@pytest.mark.xfail(strict=True, reason=SDK_ECHO)
def test_an_unknown_uri_is_not_echoed(
    rpc: Rpc, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.DEBUG)
    body = rpc("resources/read", {"uri": f"policy://{SECRET_MARKER}"})
    assert "error" in body
    assert SECRET_MARKER not in _everything(body, caplog, capsys)


@pytest.mark.xfail(strict=True, reason=SDK_ECHO)
def test_an_unknown_prompt_name_is_not_echoed(
    rpc: Rpc, caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.DEBUG)
    body = rpc("prompts/get", {"name": SECRET_MARKER, "arguments": {}})
    assert "error" in body
    assert SECRET_MARKER not in _everything(body, caplog, capsys)
