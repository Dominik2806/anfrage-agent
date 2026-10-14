"""Tests der Begrenzung des progressToken (F10, ADR 0007): hoffmann_data.progress.progress_for.

Das SDK schickt den Token des Clients in jeder Meldung zurueck, und zwar den Wert aus den rohen `params["_meta"]["progressToken"]`
(nur eine Zeichenkette oder eine Ganzzahl, nie `bool`; Beleg: test_progress_token_access.py). Der Datenservice meldet deshalb nur bei
einem Token, der
- eine Zeichenkette mit hoechstens MAX_TOKEN_LENGTH (64) Zeichen ist (auch leer, so laesst es die Spezifikation zu) oder
- eine Ganzzahl mit Betrag bis MAX_TOKEN_INT (2**53 - 1) ist.
Jeder andere Typ und jeder Wert ausserhalb der Grenzen gilt als kein Token: Der Aufruf laeuft still ohne Fortschritt, ohne Log-Zeile
und ohne Fehlermeldung. Geprueft wird der Token, den das SDK zuruecksendet (die rohen params), nicht eine zweite Quelle.

Die Unit-Tests benutzen einen Context mit rohen `params` UND `meta` (so, wie das SDK `meta` daraus baut: bool als int, 5.0 als 5).
Die Datenbank ist ein Double ohne Postgres (mcp_testkit.ScriptedDatabase). Die Grenzen stehen hier als Zahlen, nicht als Konstanten
des Pakets: Aendert jemand eine Grenze, wird ein Test rot.
"""

import ast
import asyncio
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from mcp_testkit import (
    ACTIVITY_ARGUMENTS,
    BASE_URL,
    LEAD_ARGUMENTS,
    MCP_PATH,
    MCP_SERVER_DIR,
    WRITE_PATH,
    AsgiRunner,
    ScriptedDatabase,
    customer_handler,
    make_write_handler,
    parse_sse_events,
    probe_app,
    progress_events,
    tool_call_request,
)
from starlette.testclient import TestClient

FIND_ARGUMENTS = {"query": "erika@muster.example"}
STEP_TEXTS = ["Kunde gesucht", "Ansprechpersonen geladen", "Verlauf geladen"]
WAIT = 10
MAX_INT = 2**53 - 1
ERAS = ["alt", "modern"]
PACKAGE = Path(MCP_SERVER_DIR) / "hoffmann_data"

VALID_TOKENS = [
    pytest.param("a" * 64, id="64-zeichen"),
    pytest.param("", id="leer"),
    pytest.param("t", id="1-zeichen"),
    pytest.param(7, id="int-7"),
    pytest.param(0, id="int-0"),
    pytest.param(MAX_INT, id="int-max"),
    pytest.param(-MAX_INT, id="int-minus-max"),
]
INVALID_TOKENS = [
    pytest.param("a" * 65, id="65-zeichen"),
    pytest.param("a" * 1000, id="1000-zeichen"),
    pytest.param(MAX_INT + 1, id="int-max-plus-1"),
    pytest.param(-(MAX_INT + 1), id="int-minus-max-minus-1"),
]
# Das SDK macht aus diesen Werten auf der Wire keinen Token (bool, Zahlen mit Nachkommastellen, Listen, Objekte)
NOT_A_TOKEN_ON_THE_WIRE = [
    pytest.param(True, id="bool-true"),
    pytest.param(False, id="bool-false"),
    pytest.param(5.0, id="float-5.0"),
    pytest.param(1.5, id="float-1.5"),
    pytest.param([1], id="liste"),
    pytest.param({"a": 1}, id="objekt"),
    pytest.param(b"abc", id="bytes"),
]


@contextmanager
def _client(env: dict[str, str]) -> Iterator[TestClient]:
    from hoffmann_data.config import load_config
    from hoffmann_data.server import create_app

    app = create_app(load_config(env), database=ScriptedDatabase(customer_handler, []))
    with TestClient(app, base_url=BASE_URL) as client:
        yield client


def _final(response: Any) -> dict[str, Any]:
    """Die letzte JSON-RPC-Nachricht einer Antwort: Strom (data:-Ereignisse) oder ein JSON-Dokument."""
    text = response.text
    if "data:" in text:
        return parse_sse_events(text)[-1]  # type: ignore[no-any-return]
    return response.json()  # type: ignore[no-any-return]


# progress_for mit einem Context-Double


class _RequestContext:
    def __init__(self, meta: Any, params: Any) -> None:
        self.meta = meta
        self.params = params


class FakeContext:
    def __init__(self, *, meta: Any = None, params: Any = None) -> None:
        self.request_context = _RequestContext(meta, params)

    async def report_progress(
        self, progress: float, total: float | None = None, message: str | None = None
    ) -> None:
        return None


def wire_context(raw: Any) -> FakeContext:
    """Context fuer den Token `raw` auf der Wire: params wie gesendet, meta so, wie das SDK es daraus baut."""
    if isinstance(raw, bool):
        coerced: Any = int(raw)
    elif isinstance(raw, float) and raw.is_integer():
        coerced = int(raw)
    else:
        coerced = raw
    return FakeContext(meta={"progress_token": coerced}, params={"_meta": {"progressToken": raw}})


class _OutsideOfARequest:
    """Wie ein Context ausserhalb einer Anfrage: `request_context` wirft (SDK: ValueError mit festem Text)."""

    @property
    def request_context(self) -> Any:
        raise ValueError("Context is not available outside of a request")


@pytest.mark.parametrize(
    "context",
    [
        pytest.param(FakeContext(meta=None, params=None), id="meta-und-params-none"),
        pytest.param(FakeContext(meta={}, params={"_meta": {}}), id="ohne-token"),
        pytest.param(FakeContext(meta={}, params={}), id="params-ohne-meta"),
        pytest.param(
            FakeContext(meta={"anderer_schluessel": "abc"}, params={"_meta": {"anderer": "abc"}}),
            id="anderer-schluessel",
        ),
    ],
)
def test_progress_for_returns_no_progress_without_a_token(
    context: FakeContext, caplog: pytest.LogCaptureFixture
) -> None:
    from hoffmann_data.progress import NO_PROGRESS, progress_for

    caplog.set_level(logging.DEBUG)
    assert progress_for(context, total=3) is NO_PROGRESS  # type: ignore[arg-type]
    assert not caplog.records


@pytest.mark.parametrize("raw", [*INVALID_TOKENS, *NOT_A_TOKEN_ON_THE_WIRE])
def test_progress_for_returns_no_progress_for_an_unusable_wire_token(
    raw: Any, caplog: pytest.LogCaptureFixture
) -> None:
    """Auch bool (kommt in meta als int 1 an) und 5.0 (meta: 5): Auf der Wire ist es kein Token, also kein Reporter."""
    from hoffmann_data.progress import NO_PROGRESS, progress_for

    caplog.set_level(logging.DEBUG)
    assert progress_for(wire_context(raw), total=3) is NO_PROGRESS  # type: ignore[arg-type]
    assert not caplog.records, "ein ungueltiger Token bleibt still: keine Log-Zeile"


@pytest.mark.parametrize("raw", VALID_TOKENS)
def test_progress_for_returns_a_reporter_for_a_valid_wire_token(raw: Any) -> None:
    from hoffmann_data.progress import ProgressReporter, progress_for

    assert isinstance(progress_for(wire_context(raw), total=2), ProgressReporter)  # type: ignore[arg-type]


def test_progress_for_checks_the_token_the_sdk_sends_back_not_a_second_source() -> None:
    """M1: Steht in meta etwas anderes als auf der Wire (ein Extra-Schluessel `progress_token` neben `progressToken`),
    entscheidet der rohe Wert der Wire, denn den schickt das SDK zurueck."""
    from hoffmann_data.progress import NO_PROGRESS, ProgressReporter, progress_for

    huge = "x" * (200 * 1024)
    short_in_meta = FakeContext(
        meta={"progress_token": "ok"}, params={"_meta": {"progressToken": huge}}
    )
    assert progress_for(short_in_meta, total=3) is NO_PROGRESS  # type: ignore[arg-type]
    huge_in_meta = FakeContext(
        meta={"progress_token": huge}, params={"_meta": {"progressToken": "ok"}}
    )
    assert isinstance(progress_for(huge_in_meta, total=3), ProgressReporter)  # type: ignore[arg-type]
    snake_only = FakeContext(
        meta={"progress_token": "ok"}, params={"_meta": {"progress_token": "ok"}}
    )
    assert progress_for(snake_only, total=3) is NO_PROGRESS  # type: ignore[arg-type]


def test_progress_for_outside_of_a_request_returns_no_progress_and_logs_only_the_class(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Best-Effort: Auch ein Fehler beim Lesen des Contexts bricht den Aufruf nicht ab (kein Wert, kein Ausnahmetext im Log)."""
    from hoffmann_data.progress import NO_PROGRESS, progress_for

    caplog.set_level(logging.DEBUG)
    assert progress_for(_OutsideOfARequest(), total=3) is NO_PROGRESS  # type: ignore[arg-type]
    assert len(caplog.records) <= 1
    for record in caplog.records:
        assert "ValueError" in record.getMessage()
        assert "outside of a request" not in record.getMessage()
        assert record.exc_info is None


def test_no_progress_never_hops_into_the_event_loop(monkeypatch: pytest.MonkeyPatch) -> None:
    """Ohne gueltigen Token entfaellt der Thread-Sprung: `anyio.from_thread.run` wird nie aufgerufen."""
    import anyio.from_thread
    from hoffmann_data.progress import NO_PROGRESS, NoProgress, Step, progress_for

    def forbidden(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("anyio.from_thread.run wurde aufgerufen")

    monkeypatch.setattr(anyio.from_thread, "run", forbidden)
    assert NoProgress().step(Step.INPUT_CHECKED) is None
    for raw in ("a" * 65, MAX_INT + 1, True, 5.0, None):
        reporter = progress_for(wire_context(raw), total=3)  # type: ignore[arg-type]
        assert reporter is NO_PROGRESS
        for step in Step:
            assert reporter.step(step) is None


# Ueber HTTP, beide Wege


@pytest.mark.parametrize("era", ERAS)
@pytest.mark.parametrize("value", VALID_TOKENS)
def test_a_valid_token_gets_its_messages_and_is_returned_unchanged(
    write_env: dict[str, str], token: str, value: Any, era: str
) -> None:
    payload, headers = tool_call_request(
        era, "find_customer", FIND_ARGUMENTS, progress_token=value, authorization=token
    )
    with _client(write_env) as client:
        response = client.post(MCP_PATH, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    events = parse_sse_events(response.text)
    reported = progress_events(events)
    assert [e["params"]["message"] for e in reported] == STEP_TEXTS
    assert [e["params"]["progressToken"] for e in reported] == [value] * len(STEP_TEXTS)
    assert events[-1]["result"].get("isError") is not True


@pytest.mark.parametrize("era", ERAS)
@pytest.mark.parametrize("value", INVALID_TOKENS)
def test_an_invalid_token_gets_no_message_and_the_normal_result(
    write_env: dict[str, str], token: str, value: Any, era: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG, logger="hoffmann_data")
    payload, headers = tool_call_request(
        era, "find_customer", FIND_ARGUMENTS, progress_token=value, authorization=token
    )
    plain_payload, plain_headers = tool_call_request(
        era, "find_customer", FIND_ARGUMENTS, authorization=token
    )
    with _client(write_env) as client:
        response = client.post(MCP_PATH, json=payload, headers=headers)
        plain = client.post(MCP_PATH, json=plain_payload, headers=plain_headers)
    assert response.status_code == plain.status_code == 200, response.text
    assert "notifications/progress" not in response.text
    assert _final(response)["result"] == _final(plain)["result"]
    assert _final(response)["result"].get("isError") is not True
    assert not [r for r in caplog.records if r.name.startswith("hoffmann_data")], (
        "ein ungueltiger Token bleibt still: keine Log-Zeile"
    )


@pytest.mark.parametrize("era", ERAS)
@pytest.mark.parametrize("value", INVALID_TOKENS)
def test_an_invalid_token_changes_the_frame_exactly_as_much_as_it_does_for_a_tool_without_progress(
    write_env: dict[str, str], token: str, value: Any, era: str
) -> None:
    """Ein ungueltiger Token aendert den Rahmen der Antwort nur so, wie das SDK es fuer ein Werkzeug ohne F10 tut.

    Das Referenzwerkzeug (probe_app, dieselbe crm-Funktion, kein `ctx`) misst, ob das SDK mit einem Token einen anderen Rahmen als
    ohne Token liefert. Unser Werkzeug muss sich genauso verhalten: identisch, wenn die Referenz identisch ist, abweichend nur,
    wenn auch die Referenz abweicht. Gemessen wird in jedem Lauf; der Befund steht (mit `pytest -s`) auf der Ausgabe.
    """
    from hoffmann_data import crm

    def reference(query: str) -> crm.CustomerResult:
        return crm.find_customer(ScriptedDatabase(customer_handler, []), query)

    reference_app = probe_app(reference, "find_customer", server_name="hoffmann-data")
    with_token, with_headers = tool_call_request(
        era, "find_customer", FIND_ARGUMENTS, progress_token=value
    )
    without_token, without_headers = tool_call_request(era, "find_customer", FIND_ARGUMENTS)
    with TestClient(reference_app, base_url=BASE_URL) as ref_client:
        ref_with = ref_client.post(MCP_PATH, json=with_token, headers=with_headers)
        ref_without = ref_client.post(MCP_PATH, json=without_token, headers=without_headers)

    ours_with, ours_with_headers = tool_call_request(
        era, "find_customer", FIND_ARGUMENTS, progress_token=value, authorization=token
    )
    ours_without, ours_without_headers = tool_call_request(
        era, "find_customer", FIND_ARGUMENTS, authorization=token
    )
    with _client(write_env) as client:
        ours_a = client.post(MCP_PATH, json=ours_with, headers=ours_with_headers)
        ours_b = client.post(MCP_PATH, json=ours_without, headers=ours_without_headers)
    reference_identical = ref_with.content == ref_without.content
    ours_identical = ours_a.content == ours_b.content
    print(
        f"Rahmen ({era}): Referenz mit/ohne Token identisch: {reference_identical}; "
        f"unser Werkzeug mit/ohne ungueltigem Token identisch: {ours_identical}"
    )
    assert ours_identical == reference_identical
    assert (ours_a.headers["content-type"] == ours_b.headers["content-type"]) == (
        ref_with.headers["content-type"] == ref_without.headers["content-type"]
    )


@pytest.mark.parametrize("era", ERAS)
@pytest.mark.parametrize("value", NOT_A_TOKEN_ON_THE_WIRE[:3])  # true, false, 5.0
def test_a_bool_or_a_whole_float_gets_no_message_over_http(
    write_env: dict[str, str], token: str, value: Any, era: str
) -> None:
    """`true` und 5.0: Das SDK erkennt auf der Wire keinen Token (Beleg: test_progress_token_access.py), also keine Meldung."""
    payload, headers = tool_call_request(
        era, "find_customer", FIND_ARGUMENTS, progress_token=value, authorization=token
    )
    with _client(write_env) as client:
        response = client.post(MCP_PATH, json=payload, headers=headers)
    assert response.status_code == 200, response.text
    assert "notifications/progress" not in response.text
    assert _final(response)["result"].get("isError") is not True


@pytest.mark.parametrize("era", ERAS)
def test_every_tool_applies_the_limits(
    write_env: dict[str, str], token: str, write_token: str, era: str
) -> None:
    """create_lead und log_activity benutzen dieselbe Pruefung: 64 Zeichen melden, 65 nicht."""
    from hoffmann_data.config import load_config
    from hoffmann_data.server import create_app

    app = create_app(
        load_config(write_env),
        database=ScriptedDatabase(customer_handler, []),
        write_database=ScriptedDatabase(make_write_handler(), [], write=True),
    )
    with TestClient(app, base_url=BASE_URL) as client:
        for tool, arguments in (
            ("create_lead", LEAD_ARGUMENTS),
            ("log_activity", ACTIVITY_ARGUMENTS),
        ):
            counts = {}
            for length in (64, 65):
                payload, headers = tool_call_request(
                    era, tool, arguments, progress_token="a" * length, authorization=write_token
                )
                response = client.post(WRITE_PATH, json=payload, headers=headers)
                assert response.status_code == 200, response.text
                counts[length] = len(progress_events(parse_sse_events(response.text)))
            assert counts == {64: 2, 65: 0}, (tool, counts)


# Grosser Token, Client liest nicht (beide Wege)


@pytest.mark.parametrize("era", ERAS)
def test_a_huge_token_with_a_client_that_does_not_read_holds_nothing(
    write_env: dict[str, str], token: str, era: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """200 KiB als progressToken, der Client liest nicht: Es wird nichts gesendet, der Aufruf haengt nicht, nichts bleibt offen.

    Das Zeitlimit je Meldung steht hier auf 30 s: Wuerde das Werkzeug trotz des grossen Tokens senden und am nicht lesenden Client
    haengen, kaeme die Antwort nach der Freigabe nicht innerhalb von WAIT, und die Datenbankarbeit bliebe nicht vor der Freigabe
    fertig. Die lesende Verbindung ist geschlossen, bevor der Client liest.
    """
    from hoffmann_data import progress
    from hoffmann_data.config import load_config
    from hoffmann_data.server import create_app

    monkeypatch.setattr(progress, "PROGRESS_TIMEOUT_SECONDS", 30.0)
    finished = threading.Event()
    timeline: list[str] = []
    read = ScriptedDatabase(customer_handler, timeline, on_end=lambda kind: finished.set())
    app = create_app(load_config(write_env), database=read)
    unique = "GEHEIM-4711-"
    huge = (unique * 20000)[: 200 * 1024]
    payload, headers = tool_call_request(
        era, "find_customer", FIND_ARGUMENTS, progress_token=huge, authorization=token
    )

    async def main() -> tuple[bool, Any]:
        gate = asyncio.Event()

        async def on_send(message: dict[str, Any]) -> None:
            if message["type"] == "http.response.body" and message.get("more_body"):
                await gate.wait()  # der Client liest nicht

        try:
            async with AsgiRunner(app) as runner:
                request = asyncio.create_task(
                    runner.post(MCP_PATH, payload, headers, on_send=on_send)
                )
                done = await asyncio.to_thread(finished.wait, WAIT)
                gate.set()  # erst jetzt liest der Client
                return done, await asyncio.wait_for(request, WAIT)
        finally:
            gate.set()

    done, response = asyncio.run(main())
    assert done, "die lesende Verbindung blieb offen, solange der Client nicht las"
    assert response.status == 200
    body = b"".join(response.chunks)
    assert b"notifications/progress" not in body
    assert (unique * 3).encode() not in body, "der grosse Token kam in die Antwort zurueck"
    assert timeline.count("sql:SELECT") == 3 and timeline[-1] == "close"


# Der Reporter wird nur ueber progress_for gebaut (AST)


def _calls_of(source: Path, name: str) -> list[int]:
    tree = ast.parse(source.read_text(encoding="utf-8"))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and (
            (isinstance(node.func, ast.Name) and node.func.id == name)
            or (isinstance(node.func, ast.Attribute) and node.func.attr == name)
        )
    ]


def test_the_progress_reporter_is_built_only_in_progress_py() -> None:
    offenders = {
        path.name: _calls_of(path, "ProgressReporter")
        for path in sorted(PACKAGE.glob("*.py"))
        if path.name != "progress.py" and _calls_of(path, "ProgressReporter")
    }
    assert not offenders, f"ProgressReporter( ausserhalb von progress.py: {offenders}"


def test_the_server_builds_every_reporter_with_progress_for() -> None:
    assert (
        len(_calls_of(PACKAGE / "server.py", "progress_for")) == 3
    )  # find_customer, create_lead, log_activity
    assert not _calls_of(PACKAGE / "crm.py", "ProgressReporter")
