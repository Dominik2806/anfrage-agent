"""Tests des Reporters fuer Fortschrittsmeldungen (F10): hoffmann_data/progress.py.

Festgelegt (ADR 0007):
- `Step` ist ein StrEnum mit festen Texten. Der Reporter nimmt nur einen Step, nie einen Text: Eine Meldung kann so keinen
  Wert (Eingabe, Kunden- oder Katalogdaten, Pfad) enthalten.
- `ProgressReporter(send, total)`: `send` ist die async Funktion `ctx.report_progress`, `step(step)` ist synchron und wird
  im Worker-Thread des Werkzeugs aufgerufen (anyio.from_thread.run). `progress` steigt je Meldung um 1 (1, 2, 3, ...).
- Obergrenze MAX_MESSAGES = 6 Meldungen je Aufruf, danach stumm.
- Best effort: Ein Fehler oder Zeitlimit (PROGRESS_TIMEOUT_SECONDS = 1.0, zur Aufrufzeit gelesen) beim Senden bricht nie ab;
  der Reporter schaltet sich fuer den Rest des Aufrufs ab (hoechstens ein Zeitlimit Wartezeit insgesamt). Das Log nennt nur
  die Klasse der Ausnahme (ein WARNING), nie Text oder Wert.
- NoProgress ist der Standard der Werkzeugfunktionen in crm.py und tut nichts.
- progress.py kennt keine Datenbank (kein psycopg, kein db, kein crm, kein catalog).

Die Texte mit Umlaut stehen als \\U-Escapes.
"""

import ast
import asyncio
import inspect
import logging
import re
import threading
import time
from collections.abc import Awaitable, Callable
from pathlib import Path
from typing import Any

import anyio
import anyio.to_thread
import pytest
from mcp_testkit import LEAD_ARGUMENTS, MCP_SERVER_DIR, ScriptedDatabase, make_write_handler

EXPECTED_TEXTS = {
    "INPUT_CHECKED": "Eingabe gepr\U000000fcft",
    "TRANSACTION_DONE": "Transaktion abgeschlossen",
    "RETRY_AFTER_CONFLICT": "Wiederholung nach Konflikt",
    "CUSTOMER_LOOKED_UP": "Kunde gesucht",
    "CONTACTS_LOADED": "Ansprechpersonen geladen",
    "HISTORY_LOADED": "Verlauf geladen",
}
# Die Wortliste der Paketregel (test_package_sql_rules.py), hier fuer die Meldungstexte
FORBIDDEN = re.compile(
    r"\b(INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|GRANT|COPY|CREATE|MERGE)\b", re.IGNORECASE
)
PROGRESS_MODULE = Path(MCP_SERVER_DIR) / "hoffmann_data" / "progress.py"


class Recorder:
    """Ersatz fuer ctx.report_progress: haelt (progress, total, message) fest."""

    def __init__(self) -> None:
        self.calls: list[tuple[float, float | None, str | None]] = []

    async def __call__(
        self, progress: float, total: float | None = None, message: str | None = None
    ) -> None:
        self.calls.append((progress, total, message))


GUARD_SECONDS = 10


def run_guarded(main: Callable[[], Awaitable[Any]], limit: float = GUARD_SECONDS) -> Any:
    """Fuehrt `main()` mit asyncio.run in einem Thread aus und gibt nach `limit` Sekunden auf.

    Waechter gegen Regress ohne Zeitlimit je Meldung: Ein haengender Reporter laesst den Test nach `limit`
    Sekunden fehlschlagen, statt die Sitzung bis zum Zeitlimit des Aufrufers aufzuhalten. Der Thread ist ein Daemon und
    bleibt im Fehlerfall zurueck; das beendet nur der Prozess am Ende des Laufs. Ausnahmen aus `main` laufen weiter.
    """
    result: dict[str, Any] = {}

    def runner() -> None:
        try:
            result["value"] = asyncio.run(main())  # type: ignore[arg-type]
        except BaseException as error:  # noqa: BLE001 - an den Test weiterreichen
            result["error"] = error

    thread = threading.Thread(target=runner, daemon=True)
    thread.start()
    thread.join(limit)
    if thread.is_alive():
        pytest.fail(
            f"Haengt seit mehr als {limit} s: Gibt es ein Zeitlimit je Meldung (PROGRESS_TIMEOUT_SECONDS)?",
            pytrace=False,
        )
    if "error" in result:
        raise result["error"]
    return result.get("value")


def drive(send: Any, step_names: list[str], total: float | None = 3) -> float:
    """Ruft `reporter.step` fuer die genannten Steps im Worker-Thread auf (wie das SDK) und gibt die Dauer zurueck."""
    from hoffmann_data.progress import ProgressReporter, Step

    async def main() -> float:
        reporter = ProgressReporter(send, total=total)

        def work() -> None:
            for name in step_names:
                reporter.step(getattr(Step, name))

        began = time.monotonic()
        await anyio.to_thread.run_sync(work)
        return time.monotonic() - began

    return run_guarded(main)  # type: ignore[no-any-return]


def test_step_texts_are_exactly_the_fixed_set() -> None:
    from enum import StrEnum

    from hoffmann_data.progress import Step

    assert issubclass(Step, StrEnum)
    assert {member.name: member.value for member in Step} == EXPECTED_TEXTS


def test_step_texts_contain_no_rule_keywords_no_placeholders_no_env_prefix() -> None:
    from hoffmann_data.progress import Step

    for member in Step:
        text = member.value
        assert not FORBIDDEN.search(text), text
        assert "{" not in text and "%" not in text, text
        assert not text.startswith("MCP_"), text


def test_reporter_sends_the_step_text_with_increasing_progress_and_the_total() -> None:
    recorder = Recorder()
    drive(recorder, ["CUSTOMER_LOOKED_UP", "CONTACTS_LOADED", "HISTORY_LOADED"], total=3)
    assert recorder.calls == [
        (1, 3, "Kunde gesucht"),
        (2, 3, "Ansprechpersonen geladen"),
        (3, 3, "Verlauf geladen"),
    ]


def test_reporter_accepts_only_a_step_never_a_text() -> None:
    from hoffmann_data.progress import ProgressReporter

    recorder = Recorder()

    async def main() -> None:
        reporter = ProgressReporter(recorder, total=2)
        await anyio.to_thread.run_sync(lambda: reporter.step("Kunde gesucht"))  # type: ignore[arg-type]

    with pytest.raises(TypeError):
        run_guarded(main)
    assert recorder.calls == []


def test_reporter_stops_after_the_cap_of_six_messages() -> None:
    from hoffmann_data.progress import MAX_MESSAGES

    assert MAX_MESSAGES == 6
    recorder = Recorder()
    drive(recorder, ["INPUT_CHECKED"] * 10, total=None)
    assert [call[0] for call in recorder.calls] == [1, 2, 3, 4, 5, 6]


def test_no_progress_does_nothing() -> None:
    from hoffmann_data.progress import NoProgress, Step

    assert NoProgress().step(Step.INPUT_CHECKED) is None
    assert NoProgress().step(Step.TRANSACTION_DONE) is None


@pytest.mark.parametrize("name", ["find_customer", "create_lead", "log_activity"])
def test_the_crm_functions_default_to_no_progress(name: str) -> None:
    from hoffmann_data import crm
    from hoffmann_data.progress import NoProgress

    default = inspect.signature(getattr(crm, name)).parameters["progress"].default
    assert isinstance(default, NoProgress)


@pytest.mark.parametrize("name", ["search_products", "get_product"])
def test_the_catalog_functions_have_no_progress_parameter(name: str) -> None:
    from hoffmann_data import catalog

    assert "progress" not in inspect.signature(getattr(catalog, name)).parameters


@pytest.mark.parametrize(
    "error",
    [RuntimeError, OSError, anyio.ClosedResourceError, anyio.BrokenResourceError],
)
def test_a_failing_send_never_aborts_logs_only_the_class_and_switches_the_reporter_off(
    error: type[Exception], caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.INFO)
    calls: list[int] = []

    async def failing(progress: float, total: float | None, message: str | None) -> None:
        calls.append(1)
        raise error("GEHEIMTEXT-4711")

    drive(failing, ["INPUT_CHECKED", "TRANSACTION_DONE", "RETRY_AFTER_CONFLICT"])
    assert calls == [1], "nach dem ersten Fehler wird nicht mehr gesendet"
    records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(records) == 1
    assert error.__name__ in records[0].getMessage()
    assert "GEHEIMTEXT-4711" not in caplog.text
    assert records[0].exc_info is None


def test_the_timeout_per_message_is_one_second() -> None:
    from hoffmann_data.progress import PROGRESS_TIMEOUT_SECONDS

    assert PROGRESS_TIMEOUT_SECONDS == 1.0


def test_a_hanging_send_is_dropped_after_the_timeout_and_switches_the_reporter_off(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    from hoffmann_data import progress

    monkeypatch.setattr(progress, "PROGRESS_TIMEOUT_SECONDS", 0.2)
    caplog.set_level(logging.INFO)
    calls: list[int] = []

    async def hanging(progress_value: float, total: float | None, message: str | None) -> None:
        calls.append(1)
        await anyio.sleep_forever()

    elapsed = drive(hanging, ["INPUT_CHECKED", "TRANSACTION_DONE", "RETRY_AFTER_CONFLICT"])
    assert calls == [1]
    # Untergrenze: Das Zeitlimit wurde abgewartet. Obergrenze grosszuegig (langsame Rechner): nur eine Meldung wartet.
    assert 0.15 <= elapsed < 4, elapsed
    records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(records) == 1
    assert "TimeoutError" in records[0].getMessage()


def test_the_total_wait_with_the_real_limit_is_at_most_one_timeout() -> None:
    async def hanging(progress_value: float, total: float | None, message: str | None) -> None:
        await anyio.sleep_forever()

    elapsed = drive(hanging, ["INPUT_CHECKED"] * 4)
    assert 0.9 <= elapsed < 5, (
        elapsed
    )  # vier Schritte, aber nur das erste Zeitlimit wird abgewartet


def test_the_progress_module_imports_no_database_code() -> None:
    assert PROGRESS_MODULE.is_file(), "hoffmann_data/progress.py fehlt"
    tree = ast.parse(PROGRESS_MODULE.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            base = "." * node.level + node.module
            imported.add(base)
            imported.update(f"{base}.{alias.name}" for alias in node.names)
    forbidden = {"psycopg", "hoffmann_data.db", "hoffmann_data.crm", "hoffmann_data.catalog"}
    found = sorted(
        name for name in imported if any(name == f or name.startswith(f + ".") for f in forbidden)
    )
    relative = sorted(name for name in imported if name.startswith("."))
    assert not found, found
    assert not [n for n in relative if n.lstrip(".") in {"db", "crm", "catalog"}], relative


def test_step_called_in_the_event_loop_thread_returns_normally_and_switches_the_reporter_off(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """`step()` ist fuer den Worker-Thread gedacht. Im Event-Loop-Thread kann anyio.from_thread.run nicht laufen.

    Beobachtung statt Annahme: Der Aufruf kehrt normal zurueck (Best-Effort), sendet nichts, schaltet den Reporter ab und das Log
    nennt nur die Klasse. Die Klasse selbst steht in der Meldung (mit `pytest -s` auf der Ausgabe).
    """
    from hoffmann_data.progress import ProgressReporter, Step

    caplog.set_level(logging.INFO)
    recorder = Recorder()

    async def main() -> None:
        reporter = ProgressReporter(recorder, total=2)
        assert reporter.step(Step.INPUT_CHECKED) is None
        assert reporter.step(Step.TRANSACTION_DONE) is None  # aus: kein zweiter Versuch

    run_guarded(main)
    records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    print(f"Event-Loop-Thread: {[r.getMessage() for r in records]}")
    assert recorder.calls == []
    assert len(records) == 1, [r.getMessage() for r in records]
    assert records[0].getMessage().startswith("Fortschritt nicht gesendet: ")
    assert records[0].exc_info is None


def test_create_lead_progress_keeps_increasing_across_the_retry() -> None:
    """Mit dem echten Reporter: Auch nach dem Konflikt steigt progress weiter (1, 2, 3), der Zaehler faengt nicht neu an."""
    import psycopg
    from hoffmann_data import crm, db
    from hoffmann_data.progress import ProgressReporter

    recorder = Recorder()
    attempts = {"n": 0}

    def lose_the_first_race(text: str) -> None:
        if text == db.INSERT_CUSTOMER_SQL:
            attempts["n"] += 1
            if attempts["n"] == 1:
                raise psycopg.errors.UniqueViolation("verlorenes Rennen")

    database = ScriptedDatabase(
        make_write_handler(), [], write=True, before_statement=lose_the_first_race
    )

    async def main() -> None:
        reporter = ProgressReporter(recorder, total=2)
        await anyio.to_thread.run_sync(
            lambda: crm.create_lead(database, **LEAD_ARGUMENTS, progress=reporter)
        )

    run_guarded(main)
    assert [call[0] for call in recorder.calls] == [1, 2, 3]
    assert [call[2] for call in recorder.calls] == [
        EXPECTED_TEXTS["INPUT_CHECKED"],
        EXPECTED_TEXTS["RETRY_AFTER_CONFLICT"],
        EXPECTED_TEXTS["TRANSACTION_DONE"],
    ]


def test_a_cancelled_send_returns_normally_switches_the_reporter_off_and_logs_only_the_class(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """Belegt, dass die Abbruchausnahme gefangen werden muss: Im Thread kommt asyncio.CancelledError an (BaseException, siehe test_progress_cancellation.py).

    `step()` muss normal zurueckkehren, der Reporter ist danach aus (kein zweiter Sendeversuch), das Log nennt nur die Klasse.
    """
    from hoffmann_data.progress import ProgressReporter, Step

    caplog.set_level(logging.INFO)
    started = threading.Event()
    holder: dict[str, Any] = {}
    calls: list[float] = []

    async def hanging(progress: float, total: float | None, message: str | None) -> None:
        calls.append(progress)
        holder["task"] = asyncio.current_task()
        started.set()
        await anyio.sleep_forever()

    async def main() -> str:
        reporter = ProgressReporter(hanging, total=3)

        def work() -> str:
            reporter.step(Step.INPUT_CHECKED)  # haengt, bis der Test die Aufgabe abbricht
            reporter.step(Step.TRANSACTION_DONE)  # der Reporter ist aus: kein zweiter Versuch
            return "weitergelaufen"

        work_task = asyncio.ensure_future(anyio.to_thread.run_sync(work))
        assert await asyncio.to_thread(started.wait, 5), "send wurde nicht aufgerufen"
        holder["task"].cancel()
        return await asyncio.wait_for(work_task, 5)

    assert run_guarded(main) == "weitergelaufen"
    assert calls == [1], "nach dem Abbruch wird nicht noch einmal gesendet"
    records = [r for r in caplog.records if r.levelno >= logging.WARNING]
    assert len(records) == 1
    assert "CancelledError" in records[0].getMessage()
    assert records[0].exc_info is None
