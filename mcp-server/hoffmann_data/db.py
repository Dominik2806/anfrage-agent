"""Datenbankzugriff des Datenservice hoffmann-data (F07).

Nur dieses Modul importiert den Datenbanktreiber (psycopg); alle anderen Module bekommen die Verbindung
von hier (Test: test_mcp_no_leak.py). Der Dienst liest nur (Auftrag 5.1):
- Jeder Aufruf öffnet eine eigene Verbindung (kein Pool) und schließt sie am Ende. Es wird nie committet.
- Die Verbindung ist schreibgeschützt (read_only) und hat ein Zeitlimit von 5 Sekunden je Abfrage.
- prepare_threshold=None: keine serverseitig vorbereiteten Abfragen (Pooler im Transaktionsmodus).
- Fehler der Datenbank werden auf eine feste Meldung abgebildet. Meldung, Log und Ausnahmekette enthalten
  weder Host noch Benutzer noch Datenbankname noch URL noch den Text der Ausnahme.
"""

import logging
from collections.abc import Iterator, Mapping
from contextlib import AbstractContextManager, contextmanager
from typing import Any, Protocol

import psycopg
from mcp.server.mcpserver.exceptions import ToolError
from psycopg.rows import dict_row

logger = logging.getLogger(__name__)

CONNECT_TIMEOUT_SECONDS = 5
STATEMENT_TIMEOUT_SQL = "SET LOCAL statement_timeout = '5s'"

NOT_CONFIGURED = "Die Datenbank ist nicht konfiguriert."
DATABASE_ERROR = "Die Datenbank ist nicht erreichbar oder die Abfrage ist fehlgeschlagen."


class ConnectionSource(Protocol):
    """Alles, was pro Aufruf eine Verbindung herausgibt: die echte Database und das Test-Double."""

    def connection(self) -> AbstractContextManager[Any]: ...


class Database:
    """Verbindungsquelle für eine Datenbank-URL. Der Konstruktor verbindet nicht."""

    def __init__(self, url: str, **connect_kwargs: Any) -> None:
        self._url = url
        self._connect_kwargs = connect_kwargs

    def __repr__(self) -> str:
        # Keine URL: Sie enthält Benutzer, Passwort und Host
        return "Database()"

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection[Any]]:
        """Öffnet eine eigene, schreibgeschützte Verbindung und schließt sie am Ende (Rollback, nie Commit)."""
        # Unsere Werte stehen zuletzt und gewinnen gegen connect_kwargs und gegen Parameter in der URL
        options = {
            **self._connect_kwargs,
            "connect_timeout": CONNECT_TIMEOUT_SECONDS,
            "prepare_threshold": None,
        }
        connection = psycopg.connect(self._url, **options)
        try:
            # Vor der ersten Anweisung setzen: Die Transaktion beginnt dann als BEGIN READ ONLY
            connection.read_only = True
            connection.execute(STATEMENT_TIMEOUT_SQL)
            yield connection
        finally:
            connection.close()


def _log_failure(exc: Exception) -> None:
    """Ein WARNING mit Fehlerklasse und SQLSTATE, nie mit dem Text der Ausnahme und ohne Traceback."""
    sqlstate = exc.sqlstate if isinstance(exc, psycopg.Error) else None
    detail = f" (SQLSTATE {sqlstate})" if sqlstate else ""
    logger.warning("Datenbankfehler: %s%s", type(exc).__name__, detail)


@contextmanager
def read_connection(database: ConnectionSource | None) -> Iterator[Any]:
    """Verbindung für einen lesenden Werkzeugaufruf.

    - Ohne Datenbank: ToolError(NOT_CONFIGURED), nie Scheindaten.
    - Ein ToolError aus dem Block (z. B. ungültige Eingabe) läuft unverändert durch.
    - Jeder andere Fehler (psycopg.Error, unerwartete Ausnahme, auch beim Verbinden) wird geloggt
      (Klasse und SQLSTATE) und als ToolError(DATABASE_ERROR) ohne Ursache weitergegeben.
    """
    if database is None:
        raise ToolError(NOT_CONFIGURED)
    try:
        with database.connection() as connection:
            yield connection
    except ToolError:
        raise
    except Exception as exc:  # noqa: BLE001 - Absicht: Auch unerwartete Ausnahmen dürfen keinen Text nach außen tragen
        _log_failure(exc)
        # from None: kein __cause__ und unterdrückter Kontext, damit der Text der Ausnahme nirgends mitläuft
        raise ToolError(DATABASE_ERROR) from None


def fetch_all(connection: Any, query: str, params: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Führt eine lesende Abfrage mit benannten Parametern aus und gibt die Zeilen als Dicts zurück.

    Werte gehen nur über `params` an die Datenbank, nie in den Abfragetext. Hier steht der Zeilentyp, damit
    andere Module keinen Treiber importieren müssen.
    """
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(query, params)
        return cursor.fetchall()
