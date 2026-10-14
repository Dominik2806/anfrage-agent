"""Tests der Werkzeuge mit Fortschrittsmeldungen (F10): crm.py direkt und die ganze App ueber HTTP.

Festgelegt (ADR 0007):
- crm.find_customer, crm.create_lead und crm.log_activity haben den Parameter `progress` (Standard NoProgress) und rufen
  `progress.step(Step.X)` auf. search_products und get_product melden nie.
- Schreib-Werkzeuge melden nur ausserhalb der Transaktion: INPUT_CHECKED nach der Eingabepruefung und vor dem Oeffnen der
  Verbindung, TRANSACTION_DONE nach dem Commit, RETRY_AFTER_CONFLICT nach dem Rollback und vor der zweiten Verbindung.
  Bei einem Fehler (Konflikt, unbekannter Kunde, Datenbankfehler) folgt kein TRANSACTION_DONE. Bei ungueltiger Eingabe
  gibt es keine Meldung.
- find_customer meldet zwischen den Abfragen (lesende Verbindung): Kunde gesucht (auch wenn keiner gefunden wurde, dann
  endet die Meldungsfolge), Ansprechpersonen geladen, Verlauf geladen.
- TRANSACTION_DONE heisst "Transaktion abgeschlossen", nicht "gespeichert": Bei einem bestehenden Kunden wird nichts
  geschrieben (Dublettenschutz F08), der Text muss in beiden Faellen stimmen.
- Ueber HTTP (beide Wege, beide Pfade): Meldungen vor dem Ergebnis, mit dem progressToken des Clients, progress 1, 2, ..., total
  = Zahl der Schritte des Normalwegs.

Die Datenbank ist ein Double mit Zeitleiste (mcp_testkit.ScriptedDatabase), die Tests brauchen kein Postgres. Die Texte mit Umlaut stehen als \\U-Escapes.
"""

import json
from collections.abc import Callable
from typing import Any

import psycopg
import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp_testkit import (
    ACTIVITY_ARGUMENTS,
    BASE_URL,
    LEAD_ARGUMENTS,
    MCP_PATH,
    WRITE_PATH,
    ScriptedDatabase,
    SpyProgress,
    customer_handler,
    make_write_handler,
    parse_sse_events,
    progress_events,
    tool_call_request,
)
from starlette.testclient import TestClient

TEXTS = {
    "INPUT_CHECKED": "Eingabe gepr\U000000fcft",
    "TRANSACTION_DONE": "Transaktion abgeschlossen",
    "RETRY_AFTER_CONFLICT": "Wiederholung nach Konflikt",
    "CUSTOMER_LOOKED_UP": "Kunde gesucht",
    "CONTACTS_LOADED": "Ansprechpersonen geladen",
    "HISTORY_LOADED": "Verlauf geladen",
}
PROGRESS_TOKEN = "fortschritt-token-0815"
INPUT = "progress:INPUT_CHECKED"
DONE = "progress:TRANSACTION_DONE"
RETRY = "progress:RETRY_AFTER_CONFLICT"


def assert_no_progress_inside_transactions(timeline: list[str]) -> None:
    """Keine Meldung zwischen "open" und dem Ende der Verbindung (commit, rollback, close)."""
    inside = False
    for entry in timeline:
        if entry == "open":
            inside = True
        elif entry in ("commit", "rollback", "close"):
            inside = False
        elif entry.startswith("progress:"):
            assert not inside, f"Meldung in der Transaktion: {timeline}"


def write_db(timeline: list[str], matches: Any = (None, None, None), **kwargs: Any) -> Any:
    return ScriptedDatabase(make_write_handler(matches), timeline, write=True, **kwargs)


# crm.py direkt: Zeitleiste aus Verbindung, Befehlen, Commit und Meldungen


def test_create_lead_reports_before_the_transaction_and_after_the_commit() -> None:
    from hoffmann_data import crm

    timeline: list[str] = []
    result = crm.create_lead(write_db(timeline), **LEAD_ARGUMENTS, progress=SpyProgress(timeline))
    assert result["customer_created"] is True
    assert timeline == [
        INPUT,
        "open",
        "sql:SELECT",
        "sql:INSERT",
        "sql:INSERT",
        "commit",
        DONE,
    ]
    assert_no_progress_inside_transactions(timeline)


@pytest.mark.parametrize(
    ("matches", "writes"),
    [((9, 9, 9), []), ((9, 9, None), ["sql:INSERT"])],
    ids=["bestehender-kunde-und-kontakt", "bestehender-kunde-neuer-kontakt"],
)
def test_create_lead_for_an_existing_customer_reports_the_same_steps(
    matches: tuple[int, int, int | None], writes: list[str]
) -> None:
    from hoffmann_data import crm

    timeline: list[str] = []
    crm.create_lead(write_db(timeline, matches), **LEAD_ARGUMENTS, progress=SpyProgress(timeline))
    assert timeline == [INPUT, "open", "sql:SELECT", *writes, "commit", DONE]


def test_create_lead_conflict_reports_no_completion() -> None:
    from hoffmann_data import crm

    timeline: list[str] = []
    with pytest.raises(ToolError):
        crm.create_lead(
            write_db(timeline, (None, 5, None)), **LEAD_ARGUMENTS, progress=SpyProgress(timeline)
        )
    assert timeline == [INPUT, "open", "sql:SELECT", "rollback"]


def test_create_lead_with_invalid_input_reports_nothing_and_opens_no_connection() -> None:
    from hoffmann_data import crm

    timeline: list[str] = []
    arguments = {**LEAD_ARGUMENTS, "contact_email": "keine-adresse"}
    with pytest.raises(ToolError):
        crm.create_lead(write_db(timeline), **arguments, progress=SpyProgress(timeline))
    assert timeline == []


def _first_customer_insert_fails(times: int) -> Callable[[str], None]:
    attempts = {"n": 0}

    def before(text: str) -> None:
        from hoffmann_data import db

        if text == db.INSERT_CUSTOMER_SQL:
            attempts["n"] += 1
            if attempts["n"] <= times:
                raise psycopg.errors.UniqueViolation("verlorenes Rennen")

    return before


def test_create_lead_reports_the_retry_after_the_rollback_and_before_the_second_connection() -> (
    None
):
    from hoffmann_data import crm

    timeline: list[str] = []
    database = write_db(timeline, before_statement=_first_customer_insert_fails(1))
    crm.create_lead(database, **LEAD_ARGUMENTS, progress=SpyProgress(timeline))
    assert timeline == [
        INPUT,
        "open",
        "sql:SELECT",
        "rollback",
        RETRY,
        "open",
        "sql:SELECT",
        "sql:INSERT",
        "sql:INSERT",
        "commit",
        DONE,
    ]
    assert_no_progress_inside_transactions(timeline)


def test_create_lead_that_loses_the_race_twice_ends_with_the_duplicate_error_and_no_completion() -> (
    None
):
    from hoffmann_data import crm
    from hoffmann_data.db import DUPLICATE_ERROR

    timeline: list[str] = []
    database = write_db(timeline, before_statement=_first_customer_insert_fails(2))
    with pytest.raises(ToolError) as caught:
        crm.create_lead(database, **LEAD_ARGUMENTS, progress=SpyProgress(timeline))
    assert str(caught.value) == DUPLICATE_ERROR
    assert timeline == [
        INPUT,
        "open",
        "sql:SELECT",
        "rollback",
        RETRY,
        "open",
        "sql:SELECT",
        "rollback",
    ]


def _log_activity(timeline: list[str], contact_email: str | None, **database: Any) -> Any:
    from hoffmann_data import crm

    return crm.log_activity(
        write_db(timeline, **database),
        ACTIVITY_ARGUMENTS["customer_domain"],
        ACTIVITY_ARGUMENTS["type"],
        ACTIVITY_ARGUMENTS["subject"],
        ACTIVITY_ARGUMENTS["summary"],
        contact_email,
        progress=SpyProgress(timeline),
    )


def test_log_activity_reports_before_the_transaction_and_after_the_commit() -> None:
    timeline: list[str] = []
    _log_activity(timeline, None)
    assert timeline == [INPUT, "open", "sql:SELECT", "sql:INSERT", "commit", DONE]
    assert_no_progress_inside_transactions(timeline)


def test_log_activity_with_a_contact_has_the_same_outer_steps() -> None:
    timeline: list[str] = []
    _log_activity(timeline, "erika@muster.example")
    assert timeline == [INPUT, "open", "sql:SELECT", "sql:SELECT", "sql:INSERT", "commit", DONE]


def test_log_activity_for_an_unknown_customer_reports_no_completion() -> None:
    from hoffmann_data import crm

    timeline: list[str] = []
    database = ScriptedDatabase(make_write_handler(customer_known=False), timeline, write=True)
    with pytest.raises(ToolError):
        crm.log_activity(
            database, "unbekannt.example", "note", "Betreff", "Text", progress=SpyProgress(timeline)
        )
    assert timeline == [INPUT, "open", "sql:SELECT", "rollback"]


def test_find_customer_reports_after_each_query_inside_the_read_connection() -> None:
    from hoffmann_data import crm

    timeline: list[str] = []
    database = ScriptedDatabase(customer_handler, timeline)
    result = crm.find_customer(database, "erika@muster.example", progress=SpyProgress(timeline))
    assert result["matched_by"] == "email"
    assert [e for e in timeline if e not in ("open", "close")] == [
        "sql:SELECT",
        "progress:CUSTOMER_LOOKED_UP",
        "sql:SELECT",
        "progress:CONTACTS_LOADED",
        "sql:SELECT",
        "progress:HISTORY_LOADED",
    ]


def test_find_customer_for_an_unknown_customer_ends_after_the_first_step() -> None:
    from hoffmann_data import crm

    timeline: list[str] = []
    database = ScriptedDatabase(customer_handler, timeline)
    result = crm.find_customer(database, "unbekannt.example", progress=SpyProgress(timeline))
    assert result == {"customer": None, "matched_by": None}
    assert [e for e in timeline if e not in ("open", "close")] == [
        "sql:SELECT",
        "sql:SELECT",
        "progress:CUSTOMER_LOOKED_UP",
    ]


def test_find_customer_with_invalid_input_reports_nothing() -> None:
    from hoffmann_data import crm

    timeline: list[str] = []
    with pytest.raises(ToolError):
        crm.find_customer(
            ScriptedDatabase(customer_handler, timeline), "", progress=SpyProgress(timeline)
        )
    assert timeline == []


# Die ganze App ueber HTTP (im Speicher): beide Pfade, beide Wege


class Served:
    def __init__(self, client: TestClient, tokens: dict[str, str]) -> None:
        self.client = client
        self.tokens = tokens
        self.read_timeline: list[str] = []
        self.write_timeline: list[str] = []


@pytest.fixture
def served(write_env: dict[str, str], token: str, write_token: str) -> Any:
    from hoffmann_data.config import load_config
    from hoffmann_data.server import create_app

    read_timeline: list[str] = []
    write_timeline: list[str] = []
    app = create_app(
        load_config(write_env),
        database=ScriptedDatabase(customer_handler, read_timeline),
        write_database=write_db(write_timeline),
    )
    with TestClient(app, base_url=BASE_URL) as client:
        state = Served(client, {MCP_PATH: token, WRITE_PATH: write_token})
        state.read_timeline = read_timeline
        state.write_timeline = write_timeline
        yield state


CASES = [
    (
        "find_customer",
        MCP_PATH,
        {"query": "erika@muster.example"},
        ["CUSTOMER_LOOKED_UP", "CONTACTS_LOADED", "HISTORY_LOADED"],
    ),
    ("create_lead", WRITE_PATH, LEAD_ARGUMENTS, ["INPUT_CHECKED", "TRANSACTION_DONE"]),
    ("log_activity", WRITE_PATH, ACTIVITY_ARGUMENTS, ["INPUT_CHECKED", "TRANSACTION_DONE"]),
]


@pytest.mark.parametrize("era", ["alt", "modern"])
@pytest.mark.parametrize("case", CASES, ids=[case[0] for case in CASES])
def test_a_tool_sends_its_steps_in_order_before_the_result(
    served: Served, case: tuple[str, str, dict[str, Any], list[str]], era: str
) -> None:
    tool, path, arguments, steps = case
    payload, headers = tool_call_request(
        era, tool, arguments, progress_token=PROGRESS_TOKEN, authorization=served.tokens[path]
    )
    response = served.client.post(path, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events = parse_sse_events(response.text)
    reported = progress_events(events)
    assert [e["params"]["message"] for e in reported] == [TEXTS[name] for name in steps]
    assert [e["params"]["progress"] for e in reported] == list(range(1, len(steps) + 1))
    assert {e["params"]["total"] for e in reported} == {len(steps)}
    assert {e["params"]["progressToken"] for e in reported} == {PROGRESS_TOKEN}
    assert events[-1].get("method") is None and "result" in events[-1]
    assert events[-1]["result"].get("isError") is not True
    assert all(e.get("method") == "notifications/progress" for e in events[:-1])


@pytest.mark.parametrize("era", ["alt", "modern"])
@pytest.mark.parametrize("case", CASES, ids=[case[0] for case in CASES])
def test_every_call_gets_its_own_progress_sequence_from_one_to_n(
    served: Served, case: tuple[str, str, dict[str, Any], list[str]], era: str
) -> None:
    """Der Reporter gehoert zu einem Aufruf: Auch beim fuenften Aufruf in einem Client faengt `progress` bei 1 an."""
    tool, path, arguments, steps = case
    for call in range(5):
        payload, headers = tool_call_request(
            era, tool, arguments, progress_token=PROGRESS_TOKEN, authorization=served.tokens[path]
        )
        response = served.client.post(path, json=payload, headers=headers)
        assert response.status_code == 200, (call, response.text)
        reported = progress_events(parse_sse_events(response.text))
        assert [e["params"]["progress"] for e in reported] == list(range(1, len(steps) + 1)), call
        assert [e["params"]["message"] for e in reported] == [TEXTS[name] for name in steps], call


@pytest.mark.parametrize("era", ["alt", "modern"])
@pytest.mark.parametrize(
    ("tool", "arguments"),
    [("search_products", {"query": "Gurtband"}), ("get_product", {"article_number": "FB-1001"})],
)
def test_search_products_and_get_product_stay_silent(
    write_env: dict[str, str], token: str, tool: str, arguments: dict[str, Any], era: str
) -> None:
    from hoffmann_data.config import load_config
    from hoffmann_data.server import create_app

    timeline: list[str] = []
    app = create_app(load_config(write_env), database=ScriptedDatabase(lambda t, p: [], timeline))
    payload, headers = tool_call_request(
        era, tool, arguments, progress_token=PROGRESS_TOKEN, authorization=token
    )
    with TestClient(app, base_url=BASE_URL) as client:
        response = client.post(MCP_PATH, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    assert "notifications/progress" not in response.text
    events = parse_sse_events(response.text) if "data:" in response.text else [response.json()]
    assert len(events) == 1
    assert events[0]["result"].get("isError") is not True
    assert PROGRESS_TOKEN not in response.text


# Schnittstelle: ctx ist kein Eingabeparameter

EXPECTED_PROPERTIES = {
    "search_products": {"query", "limit"},
    "get_product": {"article_number"},
    "find_customer": {"query"},
    "create_lead": {
        "company_name",
        "contact_first_name",
        "contact_last_name",
        "contact_email",
        "language",
        "country",
        "contact_job_title",
        "industry",
    },
    "log_activity": {"customer_domain", "type", "subject", "summary", "contact_email"},
}


def test_ctx_appears_in_no_tool_input_schema(
    rpc_read: Callable[..., dict[str, Any]], rpc_write: Callable[..., dict[str, Any]]
) -> None:
    tools = {
        tool["name"]: tool
        for response in (rpc_read("tools/list"), rpc_write("tools/list"))
        for tool in response["result"]["tools"]
    }
    assert set(tools) == set(EXPECTED_PROPERTIES)
    for name, tool in tools.items():
        schema = tool["inputSchema"]
        assert "ctx" not in json.dumps(schema), name
        assert set(schema["properties"]) == EXPECTED_PROPERTIES[name], name
        assert "ctx" not in schema.get("required", []), name
