"""Fortschrittsmeldungen des Datenservice (F10, ADR 0007): feste Texte, Best-Effort, begrenzt.

Ein Werkzeug des Datenservice ist synchron und laeuft im Worker-Thread des SDKs (anyio.to_thread.run_sync). Es sendet
Fortschritt mit `anyio.from_thread.run(ctx.report_progress, ...)`; das SDK schickt ihn nur, wenn der Client in der Anfrage
`_meta.progressToken` mitgeschickt hat, sonst ist `report_progress` ein No-op.

Regeln:
- Meldungen sind die festen Texte von `Step` (StrEnum). Der Reporter nimmt nur einen `Step`, nie einen Text: Eine Meldung
  kann so keinen Wert enthalten (Eingabe, Kunden- oder Katalogdaten, Pfad).
- Best effort: Ein Fehler oder Zeitlimit beim Senden bricht den Aufruf nie ab und aendert sein Ergebnis nicht. Der Reporter
  schaltet sich danach fuer den Rest des Aufrufs ab (hoechstens ein Zeitlimit Wartezeit insgesamt). Das Log nennt nur die
  Klasse der Ausnahme, nie Text oder Wert.
- Zeitlimit je Meldung `PROGRESS_TIMEOUT_SECONDS` (Konstante, keine Umgebungsvariable). Es begrenzt, wie lange ein Client, der
  nicht liest, den Worker-Thread aufhalten kann (auf dem modernen Weg ist der Strom ohne Puffer).
- Hoechstens `MAX_MESSAGES` Meldungen je Aufruf; `progress` steigt mit jeder Meldung um 1.
- Dieses Modul kennt keine Datenbank. Die Schreib-Werkzeuge melden nie innerhalb der Transaktion (crm.py).
"""

import asyncio
import enum
import logging
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Protocol

import anyio
import anyio.from_thread
from mcp.shared.jsonrpc_dispatcher import progress_token_from_params

if TYPE_CHECKING:
    from mcp.server.mcpserver import Context

logger = logging.getLogger(__name__)

PROGRESS_TIMEOUT_SECONDS = 1.0
MAX_MESSAGES = 6
# Grenzen des progressToken des Clients (Betriebsgrenzen, nicht aus der Spezifikation): siehe progress_for
MAX_TOKEN_LENGTH = 64
MAX_TOKEN_INT = 2**53 - 1

# Zahl der Schritte des Normalwegs (Wert von `total`)
FIND_CUSTOMER_TOTAL = 3
WRITE_TOTAL = 2


class Step(enum.StrEnum):
    """Die festen Meldungen. Neue Texte nur hier, ohne Werte und ohne Schluesselwoerter der Paketregel."""

    INPUT_CHECKED = "Eingabe gepr\U000000fcft"
    TRANSACTION_DONE = "Transaktion abgeschlossen"
    RETRY_AFTER_CONFLICT = "Wiederholung nach Konflikt"
    CUSTOMER_LOOKED_UP = "Kunde gesucht"
    CONTACTS_LOADED = "Ansprechpersonen geladen"
    HISTORY_LOADED = "Verlauf geladen"


class Progress(Protocol):
    """Was die Werkzeugfunktionen in crm.py brauchen: einen Schritt melden."""

    def step(self, step: Step) -> None: ...


class NoProgress:
    """Standard der Werkzeugfunktionen: meldet nichts."""

    def step(self, step: Step) -> None:
        return None


# Zustandslos, darum als Standardwert der Werkzeugfunktionen teilbar (kein Funktionsaufruf im Parameterstandard, Ruff B008)
NO_PROGRESS = NoProgress()

SendProgress = Callable[[float, float | None, str | None], Awaitable[None]]


class ProgressReporter:
    """Sendet die Schritte eines Werkzeugaufrufs aus dem Worker-Thread an den Client.

    `send` ist die async Funktion `ctx.report_progress(progress, total, message)`. Ein Reporter gehoert zu genau einem Aufruf.
    """

    def __init__(self, send: SendProgress, total: float | None) -> None:
        self._send = send
        self._total = total
        self._sent = 0
        self._active = True

    def step(self, step: Step) -> None:
        if not isinstance(step, Step):
            raise TypeError("step erwartet einen Step")
        if not self._active or self._sent >= MAX_MESSAGES:
            return
        self._sent += 1
        progress = self._sent

        async def deliver() -> None:
            with anyio.fail_after(PROGRESS_TIMEOUT_SECONDS):
                await self._send(progress, self._total, step.value)

        try:
            anyio.from_thread.run(deliver)
        except (Exception, asyncio.CancelledError) as error:  # noqa: BLE001 - Absicht: Fortschritt bricht nie ab
            # Zeitlimit, getrennter Client, geschlossener Strom oder abgebrochene Aufgabe: Der Aufruf laeuft weiter.
            # asyncio.CancelledError steht ausdruecklich da: Es ist eine BaseException und kommt bei einer abgebrochenen Aufgabe
            # im Worker-Thread an (belegt: tests/mcp_server/test_progress_cancellation.py); `except Exception` fienge es nicht.
            self._active = False
            logger.warning("Fortschritt nicht gesendet: %s", type(error).__name__)


def progress_for(ctx: "Context", total: int) -> Progress:
    """Der Reporter für diesen Aufruf, oder `NO_PROGRESS`, wenn der Client keinen brauchbaren `progressToken` geschickt hat.

    Warum begrenzt wird: Das SDK schickt den Token des Clients in jeder Meldung zurück. Ohne Grenze könnte ein Client beliebig
    große Werte (bis zur Anfragegrenze) in unsere Antworten spielen und mit einem Client, der nicht liest, Worker-Thread und
    Verbindung länger festhalten.

    Quelle: Geprüft wird der Wert, den das SDK zurückschickt, nicht eine zweite Sicht darauf. Das SDK liest ihn aus den rohen
    `params["_meta"]["progressToken"]` mit `progress_token_from_params`; dieselbe Funktion, auf `ctx.request_context.params`
    angewandt, liefert hier denselben Wert. `ctx.request_context.meta` ist dagegen ein von Pydantic gebautes Dict und damit eine zweite
    Sicht, die vom rohen Wert abweichen kann, etwa durch einen zusätzlichen Schlüssel `progress_token` im `_meta` des Clients
    (siehe `test_progress_token_sources.py`).
    Die Funktion des SDKs liefert nur eine Zeichenkette oder eine Ganzzahl, nie `bool`, Zahlen mit Nachkommastellen, Listen oder
    Objekte (`test_progress_token_access.py`); das SDK lehnt Zahlen mit Nachkommastellen, Listen und Objekte schon vorher ab.

    Gültig ist ein Token nur als Zeichenkette bis `MAX_TOKEN_LENGTH` Zeichen (auch leer, so lässt es die Spezifikation zu) oder als
    Ganzzahl mit Betrag bis `MAX_TOKEN_INT`. Die Grenzen sind Betriebsgrenzen, nicht aus der Spezifikation. Alles andere gilt als
    kein Token.

    Ein ungültiger Token bleibt still: keine Log-Zeile, keine Meldung an den Client, der Aufruf läuft ohne Fortschritt (der Client
    merkt es am Ausbleiben der Meldungen). Ein Fehler beim Lesen des Contexts oder der Params (außerhalb einer Anfrage) bricht den
    Aufruf ebenfalls nie ab: `NO_PROGRESS`, im Log nur die Klasse der Ausnahme, kein Wert und kein Ausnahmetext.
    """
    try:
        token = progress_token_from_params(ctx.request_context.params)
    except Exception as error:  # noqa: BLE001 - Absicht: Fortschritt bricht nie ab
        logger.warning("Fortschritt: Token nicht lesbar: %s", type(error).__name__)
        return NO_PROGRESS
    if isinstance(token, str):
        valid = len(token) <= MAX_TOKEN_LENGTH
    elif isinstance(token, int):
        valid = abs(token) <= MAX_TOKEN_INT
    else:
        valid = False
    if not valid:
        return NO_PROGRESS
    return ProgressReporter(ctx.report_progress, total=total)
