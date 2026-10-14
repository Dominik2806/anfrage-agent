"""Beleg fuer F10: Welche Ausnahme kommt im Worker-Thread an, wenn eine Aufgabe abgebrochen wird?

Ohne Paketcode. Der Reporter sendet mit `anyio.from_thread.run(deliver)` aus dem Worker-Thread eines sync Werkzeugs. Hier
haengt `deliver` absichtlich (anyio.sleep_forever), und der Test bricht ab, was abzubrechen ist:

1. Die von from_thread.run gestartete Aufgabe (`deliver`) wird abgebrochen. Die Frage ist, welche Ausnahme der Thread sieht:
   eine Unterklasse von Exception (z. B. concurrent.futures.CancelledError) oder asyncio.CancelledError (BaseException).
   Davon haengt ab, ob `asyncio.CancelledError` im `except` von progress.py noetig ist (sonst toter Code).
2. Die Host-Aufgabe (die auf `anyio.to_thread.run_sync` wartet) wird abgebrochen, waehrend der Thread haengt. Die Frage ist,
   ob das den Thread freigibt (dann braucht es kein Zeitlimit je Meldung gegen diesen Fall) oder nicht.

Beleg (Lauf auf Windows, Python 3.13, anyio 4.15.1): Im Thread kommt asyncio.exceptions.CancelledError an, eine BaseException.
Beide Tests schreiben die gefundene Ausnahme samt Basisklassen in die Meldung (mit `pytest -s` auch auf die Ausgabe).
Jede Wartezeit hat ein hartes Zeitlimit.
"""

import asyncio
import contextlib
import threading
from typing import Any

import anyio
import anyio.from_thread
import anyio.to_thread

WAIT = 5


def _describe(error: BaseException | None) -> str:
    if error is None:
        return "keine Ausnahme"
    return f"{type(error).__module__}.{type(error).__qualname__} (Basisklassen: {[c.__name__ for c in type(error).__mro__]})"


async def _scenario(cancel_host: bool) -> dict[str, Any]:
    started = threading.Event()
    finished = threading.Event()
    holder: dict[str, Any] = {}
    outcome: dict[str, Any] = {"error": None, "released_by_host_cancel": None}

    async def deliver() -> None:
        holder["task"] = asyncio.current_task()
        started.set()
        await anyio.sleep_forever()

    def work() -> None:
        try:
            anyio.from_thread.run(deliver)
        except BaseException as error:  # noqa: BLE001 - Beleg: genau die Klasse festhalten, auch BaseException
            outcome["error"] = error
        finally:
            finished.set()

    host = asyncio.ensure_future(anyio.to_thread.run_sync(work))
    try:
        assert await asyncio.to_thread(started.wait, WAIT), "deliver wurde nicht gestartet"
        if cancel_host:
            host.cancel()
            outcome["released_by_host_cancel"] = await asyncio.to_thread(finished.wait, 1.0)
        holder["task"].cancel()
        assert await asyncio.to_thread(finished.wait, WAIT), "der Thread wurde nicht freigegeben"
    finally:
        task = holder.get("task")
        if task is not None and not task.done():
            task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await asyncio.wait_for(host, WAIT)
    return outcome


def test_a_cancelled_delivery_task_reaches_the_thread_as_asyncio_cancelled_error() -> None:
    """Belegt (F10): Im Worker-Thread kommt asyncio.CancelledError an, eine BaseException und keine Exception.

    Darum faengt der Reporter in progress.py ausdruecklich `(Exception, asyncio.CancelledError)`: Ein `except Exception`
    liesse die Abbruchausnahme aus dem Thread in das Werkzeug laufen.
    """
    outcome = asyncio.run(_scenario(cancel_host=False))
    error = outcome["error"]
    description = _describe(error)
    print(f"Beleg 1 (deliver abgebrochen): {description}")
    assert isinstance(error, asyncio.CancelledError), description
    assert not isinstance(error, Exception), description


def test_a_cancelled_host_task_does_not_release_the_thread() -> None:
    """Belegt, dass nur das Zeitlimit je Meldung den Thread freigibt, wenn der Host abgebrochen wird."""
    outcome = asyncio.run(_scenario(cancel_host=True))
    description = _describe(outcome["error"])
    print(
        f"Beleg 2 (Host abgebrochen): Thread freigegeben={outcome['released_by_host_cancel']}, "
        f"Ausnahme nach dem Abbruch von deliver: {description}"
    )
    assert outcome["released_by_host_cancel"] is False, (
        f"der Host-Abbruch gab den Thread frei: {description}"
    )
