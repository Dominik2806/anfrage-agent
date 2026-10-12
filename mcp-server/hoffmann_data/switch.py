"""Pfadweiche des Datenservice: zwei Apps, zwei Token (F08, ADR 0005 Punkt 3).

Die Weiche wählt allein nach dem exakten Pfad: `write_path` geht an die Schreib-App, jeder andere Pfad an die
Lese-App. Sie liest weder Body noch Kopfzeilen, prüft kein Token, ändert den Scope nicht und beantwortet nie selbst
eine Anfrage; die Token-Prüfung liegt in der BearerTokenMiddleware der gewählten App (auth.py).

Die Weiche behandelt den Lifespan-Scope selbst: Jeder Session-Manager des SDKs darf nur einmal laufen, deshalb startet
sie beide in fester Reihenfolge (Lese-, dann Schreib-Manager) und baut sie in umgekehrter Reihenfolge ab. Die beiden
Apps bekommen den Lifespan-Scope nie. Fehler werden mit festen Texten gemeldet (nie der Text der Ausnahme) und genau
einmal mit der Klasse der Ausnahme geloggt; ohne Fehler und bei Anfragen schreibt die Weiche nichts ins Log.
"""

import logging
from collections.abc import Callable, Sequence
from contextlib import AbstractAsyncContextManager, AsyncExitStack
from typing import Any

from starlette.types import ASGIApp, Receive, Scope, Send

logger = logging.getLogger(__name__)

# Feste Texte für uvicorn: nie der Text der Ausnahme, nie ein Token
START_FAILED = "Start des Datenservice fehlgeschlagen."
STOP_FAILED = "Beenden des Datenservice fehlgeschlagen."


class PathSwitch:
    """Wählt allein nach dem exakten Pfad: `write_path` -> Schreib-App, alles andere -> Lese-App."""

    def __init__(
        self,
        read_app: ASGIApp,
        write_app: ASGIApp,
        *,
        write_path: str,
        lifespans: Sequence[Callable[[], AbstractAsyncContextManager[Any]]],
    ) -> None:
        self._read_app = read_app
        self._write_app = write_app
        self._write_path = write_path
        self._lifespans = tuple(lifespans)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        kind = scope["type"]
        if kind == "lifespan":
            await self._lifespan(receive, send)
        elif kind in ("http", "websocket"):
            app = self._write_app if scope.get("path") == self._write_path else self._read_app
            # scope, receive und send unverändert: weder Body noch Kopfzeilen werden gelesen
            await app(scope, receive, send)
        # Andere Scope-Typen werden nicht weitergegeben (wie BearerTokenMiddleware), ohne Fehler

    async def _lifespan(self, receive: Receive, send: Send) -> None:
        await receive()  # lifespan.startup
        started = False
        try:
            async with AsyncExitStack() as stack:
                # Lese-Manager zuerst, dann Schreib-Manager; die Fabrik läuft bei jedem Lauf neu
                for factory in self._lifespans:
                    await stack.enter_async_context(factory())
                await send({"type": "lifespan.startup.complete"})
                started = True
                await receive()  # lifespan.shutdown
            # Der Stack ist verlassen: Schreib-Manager zuerst, dann Lese-Manager beendet
        except Exception as exc:  # noqa: BLE001 - Absicht: Startfehler jeder Art werden gemeldet
            # BaseException (Abbruch, CancelledError) läuft durch
            # Ein WARNING mit der Klasse, nie mit dem Text der Ausnahme und ohne Traceback
            logger.warning("Lifespan-Fehler: %s", type(exc).__name__)
            if started:
                await send({"type": "lifespan.shutdown.failed", "message": STOP_FAILED})
            else:
                await send({"type": "lifespan.startup.failed", "message": START_FAILED})
            return
        await send({"type": "lifespan.shutdown.complete"})
