"""Sicherheits- und Leitplankentests der Fortschrittsmeldungen (F10).

Festgelegt (ADR 0007):
- Der Token-Schutz gilt unveraendert vor allem anderen: Ohne oder mit falschem Token, mit dem Token des anderen Pfads und bei
  jedem Wert des Headers MCP-Protocol-Version gibt es 401, nie ein Byte Fortschritt.
- Ohne progressToken ist die Antwort byte-identisch zu einem Werkzeug ohne F10 (Vergleich mit einer Referenz-App, die
  dieselbe crm-Funktion ohne `ctx` und ohne `progress` aufruft).
- Meldungen sind feste Texte: Eingabewerte kommen nie in eine Meldung, nie ins Log, nie in stdout oder stderr.
- Der progressToken kommt vom Client und wird protokollgemaess in jeder notifications/progress zurueckgegeben. Das ist die
  bewusste Ausnahme zur Regel "keine Eingabewerte in Antworten" (ADR 0007). Er steht nur in diesen Meldungen, nie im
  Ergebnis, im Log, in stdout oder stderr.
- Ein Datenbankfehler nach einer gesendeten Meldung ergibt HTTP 200 mit isError und der festen Meldung, genau ein WARNING
  (Klasse und SQLSTATE aus db.py), kein Text der Ausnahme.
"""

import json
import logging
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

import pytest
from mcp_testkit import (
    ACTIVITY_ARGUMENTS,
    BASE_URL,
    LEAD_ARGUMENTS,
    MCP_PATH,
    SECRET_MARKER,
    WRITE_PATH,
    ScriptedDatabase,
    customer_handler,
    make_write_handler,
    parse_sse_events,
    probe_app,
    progress_events,
    tool_call_request,
)
from starlette.testclient import TestClient

PROGRESS_TOKEN = "fortschritt-token-0815"
FIND_ARGUMENTS = {"query": "erika@muster.example"}
LOWER_MARKER = SECRET_MARKER.lower()


@contextmanager
def _client(
    env: dict[str, str], read: ScriptedDatabase | None = None, write: ScriptedDatabase | None = None
) -> Iterator[TestClient]:
    from hoffmann_data.config import load_config
    from hoffmann_data.server import create_app

    app = create_app(load_config(env), database=read, write_database=write)
    with TestClient(app, base_url=BASE_URL) as client:
        yield client


# Token-Schutz


@pytest.mark.parametrize("version", [None, "2026-07-28", "2025-06-18", "garbage"])
@pytest.mark.parametrize(
    ("path", "kind"),
    [
        (MCP_PATH, "keiner"),
        (MCP_PATH, "falsch"),
        (MCP_PATH, "schreib-token"),
        (WRITE_PATH, "keiner"),
        (WRITE_PATH, "falsch"),
        (WRITE_PATH, "lese-token"),
    ],
)
def test_a_request_without_the_right_token_gets_401_and_no_progress_byte(
    dual_client: TestClient,
    token: str,
    write_token: str,
    path: str,
    kind: str,
    version: str | None,
) -> None:
    secret = {
        "keiner": None,
        "falsch": "x" * 43,
        "schreib-token": write_token,
        "lese-token": token,
    }[kind]
    tool = "find_customer" if path == MCP_PATH else "create_lead"
    arguments = FIND_ARGUMENTS if path == MCP_PATH else LEAD_ARGUMENTS
    payload, headers = tool_call_request(
        "alt", tool, arguments, progress_token=PROGRESS_TOKEN, authorization=secret
    )
    if version is not None:
        headers["MCP-Protocol-Version"] = version
    response = dual_client.post(path, json=payload, headers=headers)
    assert response.status_code == 401
    assert b"progress" not in response.content.lower()
    assert PROGRESS_TOKEN.encode() not in response.content


# Ohne progressToken: byte-identisch zu einem Werkzeug ohne F10


@pytest.mark.parametrize("era", ["alt", "modern"])
@pytest.mark.parametrize("tool", ["find_customer", "create_lead", "log_activity"])
def test_without_a_progress_token_the_response_is_byte_identical_to_a_tool_without_progress(
    write_env: dict[str, str], token: str, write_token: str, tool: str, era: str
) -> None:
    from hoffmann_data import crm

    if tool == "find_customer":
        # Der Servername steht im modernen Weg in _meta (serverInfo.name): Die Referenz muss ihn wie der echte Server tragen
        path, arguments, secret, server_name = MCP_PATH, FIND_ARGUMENTS, token, "hoffmann-data"

        def reference(query: str) -> crm.CustomerResult:
            return crm.find_customer(ScriptedDatabase(customer_handler, []), query)

    elif tool == "create_lead":
        path, arguments, secret = WRITE_PATH, LEAD_ARGUMENTS, write_token
        server_name = "hoffmann-data-write"  # der Schreib-Pfad hat einen eigenen Server (server.create_write_server)

        def reference(  # type: ignore[misc]
            company_name: str,
            contact_first_name: str,
            contact_last_name: str,
            contact_email: str,
            language: str,
            country: str,
        ) -> crm.LeadResult:
            database = ScriptedDatabase(make_write_handler(), [], write=True)
            return crm.create_lead(
                database,
                company_name,
                contact_first_name,
                contact_last_name,
                contact_email,
                language,
                country,
            )

    else:
        path, arguments, secret = WRITE_PATH, ACTIVITY_ARGUMENTS, write_token
        server_name = "hoffmann-data-write"  # der Schreib-Pfad hat einen eigenen Server (server.create_write_server)

        def reference(  # type: ignore[misc]
            customer_domain: str, type: str, subject: str, summary: str
        ) -> crm.ActivityResult:
            database = ScriptedDatabase(make_write_handler(), [], write=True)
            return crm.log_activity(database, customer_domain, type, subject, summary)

    read = ScriptedDatabase(customer_handler, [])
    write = ScriptedDatabase(make_write_handler(), [], write=True)
    payload, headers = tool_call_request(era, tool, arguments, authorization=secret)
    reference_payload, reference_headers = tool_call_request(era, tool, arguments)
    with _client(write_env, read, write) as real:
        actual = real.post(path, json=payload, headers=headers)
    reference_app = probe_app(reference, tool, server_name=server_name)
    with TestClient(reference_app, base_url=BASE_URL) as expected_client:
        expected = expected_client.post(MCP_PATH, json=reference_payload, headers=reference_headers)
    assert actual.status_code == expected.status_code == 200
    assert actual.headers["content-type"] == expected.headers["content-type"]
    assert actual.content == expected.content
    assert b"progress" not in actual.content.lower()


# Meldungen enthalten keine Werte; der progressToken steht nur in den Meldungen


def _secret_arguments(tool: str) -> tuple[str, dict[str, Any]]:
    if tool == "find_customer":
        return MCP_PATH, {"query": f"{LOWER_MARKER}.example"}
    if tool == "create_lead":
        return WRITE_PATH, {
            "company_name": f"{SECRET_MARKER} GmbH",
            "contact_first_name": SECRET_MARKER,
            "contact_last_name": SECRET_MARKER,
            "contact_email": f"kontakt@{LOWER_MARKER}.example",
            "language": "de",
            "country": "de",
        }
    return WRITE_PATH, {
        "customer_domain": f"{LOWER_MARKER}.example",
        "type": "note",
        "subject": SECRET_MARKER,
        "summary": SECRET_MARKER,
    }


@pytest.mark.parametrize("era", ["alt", "modern"])
@pytest.mark.parametrize("tool", ["find_customer", "create_lead", "log_activity"])
def test_input_values_and_the_progress_token_stay_out_of_messages_log_and_output(
    write_env: dict[str, str],
    token: str,
    write_token: str,
    tool: str,
    era: str,
    caplog: pytest.LogCaptureFixture,
    capfd: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.INFO)
    path, arguments = _secret_arguments(tool)
    secret = token if path == MCP_PATH else write_token
    read = ScriptedDatabase(customer_handler, [])
    write = ScriptedDatabase(make_write_handler(), [], write=True)
    payload, headers = tool_call_request(
        era, tool, arguments, progress_token=PROGRESS_TOKEN, authorization=secret
    )
    with _client(write_env, read, write) as client:
        response = client.post(path, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    events = parse_sse_events(response.text)
    reported = progress_events(events)
    assert reported, "keine Fortschrittsmeldung"
    for event in reported:
        assert LOWER_MARKER not in json.dumps(event).lower()
    # Der Token steht in jeder Meldung (Protokoll) und nirgends sonst: nicht im Ergebnis, nicht im Log, nicht in stdout/stderr
    assert response.text.count(PROGRESS_TOKEN) == len(reported)
    assert PROGRESS_TOKEN not in json.dumps(events[-1])
    captured = capfd.readouterr()
    for text in (caplog.text, captured.out, captured.err):
        assert PROGRESS_TOKEN not in text
        assert LOWER_MARKER not in text.lower()


# Datenbankfehler nach einer gesendeten Meldung


@pytest.mark.parametrize("era", ["alt", "modern"])
def test_a_database_error_after_a_sent_message_gives_200_is_error_and_exactly_one_warning(
    write_env: dict[str, str], write_token: str, era: str, caplog: pytest.LogCaptureFixture
) -> None:
    from hoffmann_data import db

    caplog.set_level(logging.INFO)

    def fail_on_insert(text: str) -> None:
        if text == db.INSERT_ACTIVITY_SQL:
            raise RuntimeError("GEHEIMTEXT-4711")

    write = ScriptedDatabase(make_write_handler(), [], write=True, before_statement=fail_on_insert)
    payload, headers = tool_call_request(
        era,
        "log_activity",
        ACTIVITY_ARGUMENTS,
        progress_token=PROGRESS_TOKEN,
        authorization=write_token,
    )
    with _client(write_env, write=write) as client:
        response = client.post(WRITE_PATH, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    events = parse_sse_events(response.text)
    assert [e["params"]["message"] for e in progress_events(events)] == ["Eingabe gepr\U000000fcft"]
    result = events[-1]["result"]
    assert result["isError"] is True
    assert db.DATABASE_ERROR in result["content"][0]["text"]
    assert "GEHEIMTEXT-4711" not in response.text
    assert "GEHEIMTEXT-4711" not in caplog.text
    own = [r for r in caplog.records if r.name.startswith("hoffmann_data")]
    assert len([r for r in own if r.levelno == logging.WARNING]) == 1
    assert not [r for r in own if r.levelno >= logging.ERROR]
