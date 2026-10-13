"""Tests der Lifespan-Behandlung der Pfadweiche hoffmann_data/switch.py (F08, ADR 0005 Punkt 3).

Schnittstelle, die diese Tests festlegen:
- Die Weiche behandelt den Scope "lifespan" selbst und gibt ihn an keine App weiter. Sie ruft die Fabriken in
  `lifespans` der Reihe nach auf (Lese-Manager, dann Schreib-Manager), betritt sie mit einem AsyncExitStack und sendet
  danach `lifespan.startup.complete`. Auf `lifespan.shutdown` verlässt sie die Kontexte in umgekehrter Reihenfolge und
  sendet `lifespan.shutdown.complete`.
- Scheitert ein Start, räumt der Stack die bereits gestarteten Kontexte ab; gesendet wird `lifespan.startup.failed`
  mit einer festen Meldung (kein Text der Ausnahme, kein Token) und kein `startup.complete`. Scheitert das Beenden
  eines Kontexts, werden die übrigen trotzdem beendet; gesendet wird `lifespan.shutdown.failed`.
- Die Weiche loggt nichts, außer bei einem Lifespan-Fehler: dann genau ein WARNING von `hoffmann_data.switch` mit dem
  Klassennamen der Ausnahme, nie mit ihrem Text und ohne Traceback.
- create_app mit Schreibkonfiguration gibt die Weiche zurück und reicht ihr die `session_manager.run` beider SDK-Apps;
  ohne Schreibkonfiguration gibt es keine Weiche (die Lese-App bleibt allein, ihre Lifespan läuft wie bisher).

SDK-Verhalten, das die Tests belegen (nicht aus dem Quelltext gefolgert): ein Session-Manager läuft je Instanz nur
einmal; ohne laufenden Manager scheitert jede Anfrage; die Lifespans der Starlette-Apps werden nicht zusätzlich angestoßen.

Ohne Datenbank. Rot, bis hoffmann_data/switch.py und die neue create_app existieren (TDD).
"""

import json
import logging
from typing import Any

import pytest
from mcp_testkit import (
    BASE_URL,
    MCP_PATH,
    RPC_HEADERS,
    WRITE_PATH,
    RecordingApp,
    drive_lifespan,
    fake_lifespan,
)
from starlette.testclient import TestClient

SWITCH_LOGGER = "hoffmann_data.switch"
TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
COMPLETE = ["lifespan.startup.complete", "lifespan.shutdown.complete"]


def _switch(events: list[str], fail_on: str | None = None, **apps: Any) -> Any:
    from hoffmann_data.switch import PathSwitch

    return PathSwitch(
        apps.get("read", RecordingApp("lese")),
        apps.get("write", RecordingApp("schreib")),
        write_path=WRITE_PATH,
        lifespans=(fake_lifespan("read", events, fail_on), fake_lifespan("write", events, fail_on)),
    )


def _types(messages: list[dict[str, Any]]) -> list[str]:
    return [m["type"] for m in messages]


def _switch_records(caplog: pytest.LogCaptureFixture) -> list[logging.LogRecord]:
    return [r for r in caplog.records if r.name == SWITCH_LOGGER]


# Ablauf mit Fake-Lifespans


def test_startup_order_and_reverse_shutdown() -> None:
    events: list[str] = []
    timeline: list[str] = []
    switch = _switch(events)

    async def tapped(scope: Any, receive: Any, send: Any) -> None:
        async def tap(message: dict[str, Any]) -> None:
            timeline.append(message["type"])
            await send(message)

        await switch(scope, receive, tap)

    messages = drive_lifespan(tapped)
    assert _types(messages) == COMPLETE
    assert events == ["read:enter", "write:enter", "write:exit", "read:exit"]
    assert timeline == COMPLETE


def test_both_contexts_are_entered_before_startup_complete() -> None:
    """Erst wenn beide Manager laufen, meldet die Weiche den Start (sonst käme eine Anfrage ohne Task-Group)."""
    events: list[str] = []
    switch = _switch(events)
    seen_at_complete: list[list[str]] = []

    async def tapped(scope: Any, receive: Any, send: Any) -> None:
        async def tap(message: dict[str, Any]) -> None:
            if message["type"] == "lifespan.startup.complete":
                seen_at_complete.append(list(events))
            await send(message)

        await switch(scope, receive, tap)

    drive_lifespan(tapped)
    assert seen_at_complete == [["read:enter", "write:enter"]]


def test_a_failing_second_start_cleans_up_the_first() -> None:
    events: list[str] = []
    messages = drive_lifespan(_switch(events, fail_on="write:enter"))
    assert events == ["read:enter", "write:enter-failed", "read:exit"]
    assert _types(messages) == ["lifespan.startup.failed"]
    assert "geheimtext" not in json.dumps(messages)


def test_a_failing_first_start_never_starts_the_second() -> None:
    events: list[str] = []
    messages = drive_lifespan(_switch(events, fail_on="read:enter"))
    assert events == ["read:enter-failed"]
    assert _types(messages) == ["lifespan.startup.failed"]
    assert "geheimtext" not in json.dumps(messages)


@pytest.mark.parametrize("failing", ["read:exit", "write:exit"])
def test_a_failing_stop_of_one_still_stops_the_other(failing: str) -> None:
    events: list[str] = []
    messages = drive_lifespan(_switch(events, fail_on=failing))
    assert events == ["read:enter", "write:enter", "write:exit", "read:exit"]
    assert _types(messages) == ["lifespan.startup.complete", "lifespan.shutdown.failed"]
    assert "geheimtext" not in json.dumps(messages)


def test_failure_messages_are_fixed_texts_without_exception_text() -> None:
    texts: list[str] = []
    for fail_on in ("read:enter", "write:enter", "read:exit", "write:exit"):
        messages = drive_lifespan(_switch([], fail_on=fail_on))
        failed = [m for m in messages if m["type"].endswith(".failed")]
        assert len(failed) == 1
        text = failed[0]["message"]
        assert isinstance(text, str) and text.strip()
        assert "geheimtext" not in text and "RuntimeError" not in text
        texts.append(text)
    assert len(set(texts[:2])) == 1, "die Startmeldung ist fest"
    assert len(set(texts[2:])) == 1, "die Beendenmeldung ist fest"


def test_the_lifespan_scope_is_not_forwarded_to_the_apps() -> None:
    read, write = RecordingApp("lese"), RecordingApp("schreib")
    messages = drive_lifespan(_switch([], read=read, write=write))
    assert _types(messages) == COMPLETE
    assert read.calls == [] and write.calls == []


def test_the_lifespan_factories_run_again_for_each_run() -> None:
    """Die Weiche ruft die Fabrik je Lauf neu auf (ein Manager-Kontext ist nicht wiederverwendbar)."""
    events: list[str] = []
    switch = _switch(events)
    drive_lifespan(switch)
    drive_lifespan(switch)
    assert events == [
        "read:enter",
        "write:enter",
        "write:exit",
        "read:exit",
        "read:enter",
        "write:enter",
        "write:exit",
        "read:exit",
    ]


# Log: nur bei Lifespan-Fehlern, nur die Klasse


@pytest.mark.parametrize("fail_on", ["read:enter", "write:enter", "read:exit", "write:exit"])
def test_the_switch_logs_only_the_exception_class_on_lifespan_errors(
    caplog: pytest.LogCaptureFixture, fail_on: str
) -> None:
    caplog.set_level(logging.DEBUG)
    drive_lifespan(_switch([], fail_on=fail_on))
    records = _switch_records(caplog)
    assert len(records) == 1, [r.getMessage() for r in records]
    record = records[0]
    assert record.levelno == logging.WARNING
    assert "RuntimeError" in record.getMessage()
    assert "geheimtext" not in record.getMessage()
    assert record.exc_info is None and record.exc_text is None and record.stack_info is None
    assert "geheimtext" not in "\n".join(
        f"{r.getMessage()} {r.exc_text or ''}" for r in caplog.records
    )


def test_the_switch_logs_nothing_without_errors(caplog: pytest.LogCaptureFixture) -> None:
    from hoffmann_data.switch import PathSwitch

    caplog.set_level(logging.DEBUG)
    events: list[str] = []
    switch = PathSwitch(
        RecordingApp("lese"),
        RecordingApp("schreib"),
        write_path=WRITE_PATH,
        lifespans=(fake_lifespan("read", events), fake_lifespan("write", events)),
    )
    drive_lifespan(switch)
    assert _switch_records(caplog) == []


# Mit den echten SDK-Apps


def _dual(make_app: Any, write_env: dict[str, str]) -> Any:
    return make_app(write_env)


def test_with_write_config_create_app_returns_the_switch(
    make_app: Any, write_env: dict[str, str]
) -> None:
    from hoffmann_data.switch import PathSwitch

    assert isinstance(_dual(make_app, write_env), PathSwitch)


def test_without_write_config_there_is_no_switch(make_app: Any, token: str) -> None:
    from hoffmann_data.switch import PathSwitch

    assert not isinstance(make_app({"MCP_SERVER_TOKEN": token}), PathSwitch)


def test_both_session_managers_start(
    make_app: Any,
    write_env: dict[str, str],
    read_headers: dict[str, str],
    write_headers: dict[str, str],
) -> None:
    """Eine Anfrage je Pfad gelingt nur, wenn der Manager des Pfads läuft (sonst RuntimeError der Task-Group)."""
    app = _dual(make_app, write_env)
    with TestClient(app, base_url=BASE_URL) as client:
        read = client.post(MCP_PATH, json=TOOLS_LIST, headers={**RPC_HEADERS, **read_headers})
        write = client.post(WRITE_PATH, json=TOOLS_LIST, headers={**RPC_HEADERS, **write_headers})
    assert read.status_code == 200 and write.status_code == 200


def test_both_session_managers_stop(
    make_app: Any,
    write_env: dict[str, str],
    read_headers: dict[str, str],
    write_headers: dict[str, str],
) -> None:
    """Nach dem Ende der Lifespan scheitert jede Anfrage: Die Task-Group beider Manager ist abgebaut."""
    app = _dual(make_app, write_env)
    assert _types(drive_lifespan(app)) == COMPLETE
    client = TestClient(app, base_url=BASE_URL)  # ohne `with`: keine Lifespan
    for path, headers in ((MCP_PATH, read_headers), (WRITE_PATH, write_headers)):
        with pytest.raises(RuntimeError):
            client.post(path, json=TOOLS_LIST, headers={**RPC_HEADERS, **headers})


def test_the_switch_does_not_start_the_sdk_lifespans_a_second_time(
    make_app: Any, write_env: dict[str, str]
) -> None:
    """Würde die Weiche die Lifespan der Starlette-Apps zusätzlich anstoßen, liefe jeder Manager zweimal und der
    zweite Eintritt scheiterte (ein Manager läuft je Instanz nur einmal)."""
    assert _types(drive_lifespan(_dual(make_app, write_env))) == COMPLETE


def test_a_second_run_of_the_same_app_fails_cleanly(
    make_app: Any, write_env: dict[str, str]
) -> None:
    """Belegt das SDK-Verhalten: Ein Session-Manager läuft nur einmal. Der zweite Lauf meldet startup.failed."""
    app = _dual(make_app, write_env)
    assert _types(drive_lifespan(app)) == COMPLETE
    assert _types(drive_lifespan(app)) == ["lifespan.startup.failed"]


def test_without_write_config_the_read_app_runs_alone(
    make_app: Any, token: str, read_headers: dict[str, str]
) -> None:
    app = make_app({"MCP_SERVER_TOKEN": token})
    with TestClient(app, base_url=BASE_URL) as client:
        response = client.post(MCP_PATH, json=TOOLS_LIST, headers={**RPC_HEADERS, **read_headers})
    assert response.status_code == 200
    assert _types(drive_lifespan(make_app({"MCP_SERVER_TOKEN": token}))) == COMPLETE
