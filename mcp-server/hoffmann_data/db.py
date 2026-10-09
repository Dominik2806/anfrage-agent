"""Datenbankzugriff des Datenservice hoffmann-data (F07).

Nur dieses Modul importiert den Datenbanktreiber (psycopg); alle anderen Module bekommen die Verbindung
von hier (Test: test_mcp_no_leak.py). Der Dienst liest nur (Auftrag 5.1):
- Jeder Aufruf öffnet eine eigene Verbindung (kein Pool) und schließt sie am Ende. Es wird nie committet.
- Die Verbindung ist schreibgeschützt (read_only) und hat ein Zeitlimit von 5 Sekunden je Abfrage.
- prepare_threshold=None: keine serverseitig vorbereiteten Abfragen (Pooler im Transaktionsmodus).
- Fehler der Datenbank werden auf eine feste Meldung abgebildet. Meldung, Log und Ausnahmekette enthalten
  weder Host noch Benutzer noch Datenbankname noch URL noch den Text der Ausnahme.
- Beim Start prüft verify_read_only_role, dass die Rolle der Verbindung wirklich nur lesen darf (Etappe 7).
  Die Prüfung ist eine Momentaufnahme; die Rolle (db/roles/data_service_ro.sql) bleibt die eigentliche Sperre.
"""

import logging
from collections.abc import Iterator, Mapping, Sequence
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


# Startprüfung der Rolle (Etappe 7)

# Rechtenamen: eigene Zeichenketten. Sie gehen nur als Parameter an die Datenbank, nie in den SQL-Text.
SELECT_RIGHT = "SELECT"
INSERT_RIGHT = "INSERT"
UPDATE_RIGHT = "UPDATE"
DELETE_RIGHT = "DELETE"
TRUNCATE_RIGHT = "TRUNCATE"
CREATE_RIGHT = "CREATE"

# Kurzbezeichnungen der gescheiterten Prüfungen. Sie sind der ganze Inhalt des Fehlertexts: nie Rollenname,
# Host, Benutzer, Datenbankname, URL oder Passwort.
SUPERUSER_FAILURE = "rolle-ist-superuser"
BYPASS_RLS_FAILURE = "rolle-umgeht-rls"
CREATE_FAILURE = "create-im-schema"
DATABASE_FAILURE = "datenbankfehler"
ROLE_CHECK_FAILED = "Prüfung der Datenbankrolle fehlgeschlagen: "

# Geprüfte Tabellen in der Reihenfolge der Ausgabe (die Rolle aus db/roles/data_service_ro.sql liest diese fünf)
CHECKED_TABLES = ("products", "product_fits", "customers", "contacts", "activities")
# Kurzbezeichnung "kein SELECT" je Tabelle als feste Literale: Eine Zeichenkette mit dem Wort select würde
# test_package_sql_rules.py als SQL ansehen und darf deshalb nicht per f-String oder Verkettung entstehen.
NO_SELECT_FAILURES = {
    "products": "kein-select:products",
    "product_fits": "kein-select:product_fits",
    "customers": "kein-select:customers",
    "contacts": "kein-select:contacts",
    "activities": "kein-select:activities",
}
# Schreibrechte in der Reihenfolge der Ausgabe: (Recht, Spalte aus TABLES_SQL)
_WRITE_CHECKS = (
    (INSERT_RIGHT, "can_insert"),
    (UPDATE_RIGHT, "can_update"),
    (DELETE_RIGHT, "can_delete"),
    (TRUNCATE_RIGHT, "can_truncate"),
)

# Rollenattribute und Recht auf das Schema, eine Zeile. current_user ist die Rolle der Verbindung (nach
# SET ROLE die gesetzte Rolle), nicht session_user. Ohne Schema im search_path ist can_create falsch.
ROLE_SQL = """SELECT
    r.rolsuper,
    r.rolbypassrls,
    COALESCE(
        has_schema_privilege(current_user, current_schema(), %(priv_create)s::text), false
    ) AS can_create
FROM pg_roles r
WHERE r.rolname = current_user"""

# Eine Zeile je Tabelle in der Reihenfolge von CHECKED_TABLES. to_regclass löst den Namen über den search_path
# auf; eine fehlende Tabelle ergibt NULL und damit nur present = false, keinen Datenbankfehler. INSERT und
# UPDATE zählen auch als Spaltenrecht (has_any_column_privilege), die übrigen Rechte als Tabellenrecht.
TABLES_SQL = """SELECT
    t.table_name,
    to_regclass(t.table_name) IS NOT NULL AS present,
    COALESCE(
        has_table_privilege(current_user, to_regclass(t.table_name), %(priv_select)s::text), false
    ) AS can_select,
    COALESCE(
        has_any_column_privilege(current_user, to_regclass(t.table_name), %(priv_insert)s::text), false
    ) AS can_insert,
    COALESCE(
        has_any_column_privilege(current_user, to_regclass(t.table_name), %(priv_update)s::text), false
    ) AS can_update,
    COALESCE(
        has_table_privilege(current_user, to_regclass(t.table_name), %(priv_delete)s::text), false
    ) AS can_delete,
    COALESCE(
        has_table_privilege(current_user, to_regclass(t.table_name), %(priv_truncate)s::text), false
    ) AS can_truncate
FROM unnest(%(tables)s::text[]) WITH ORDINALITY AS t(table_name, ord)
ORDER BY t.ord"""


class RoleCheckError(Exception):
    """Die Rolle der Verbindung darf mehr als lesen, kann nicht lesen oder die Prüfung ist gescheitert.

    `failed` nennt die Kurzbezeichnungen der gescheiterten Prüfungen in fester Reihenfolge. Der Text besteht nur
    aus einem festen Satz und diesen Kurzbezeichnungen.
    """

    def __init__(self, failed: Sequence[str]) -> None:
        self.failed = tuple(failed)
        super().__init__(ROLE_CHECK_FAILED + ", ".join(self.failed))


def _failed_checks(role_rows: list[dict[str, Any]], table_rows: list[dict[str, Any]]) -> list[str]:
    """Die Kurzbezeichnungen der gescheiterten Prüfungen, geordnet nach Prüfung 1 bis 6.

    Die Reihenfolge der Tabellen kommt aus ORDER BY der Abfrage. Eine fehlende Tabelle erscheint nur unter 6.
    """
    role = role_rows[0]
    present = [row for row in table_rows if row["present"]]
    failed: list[str] = []
    if role["rolsuper"]:
        failed.append(SUPERUSER_FAILURE)
    if role["rolbypassrls"]:
        failed.append(BYPASS_RLS_FAILURE)
    for row in present:
        for right, column in _WRITE_CHECKS:
            if row[column]:
                failed.append(f"schreibrecht:{right}:{row['table_name']}")
    for row in present:
        if not row["can_select"]:
            failed.append(NO_SELECT_FAILURES[row["table_name"]])
    if role["can_create"]:
        failed.append(CREATE_FAILURE)
    for row in table_rows:
        if not row["present"]:
            failed.append(f"tabelle-fehlt:{row['table_name']}")
    return failed


def verify_read_only_role(database: ConnectionSource) -> None:
    """Prüft beim Start, dass die Rolle der Verbindung nur lesen darf. Wirft RoleCheckError, sonst None.

    Prüfungen: 1 kein Superuser, 2 kein BYPASSRLS, 3 kein INSERT, UPDATE, DELETE, TRUNCATE auf den fünf Tabellen
    (INSERT und UPDATE auch nicht als Spaltenrecht), 4 SELECT auf allen fünf, 5 kein CREATE im aktuellen Schema,
    6 alle fünf Tabellen existieren. Nur lesende Abfragen, genau eine Verbindung. Scheitert die Verbindung oder
    eine Abfrage, steht im Fehler nur die Kurzbezeichnung `datenbankfehler`; geloggt wird genau einmal Klasse
    und SQLSTATE.
    """
    try:
        with database.connection() as connection:
            role_rows = fetch_all(connection, ROLE_SQL, {"priv_create": CREATE_RIGHT})
            table_rows = fetch_all(
                connection,
                TABLES_SQL,
                {
                    "tables": list(CHECKED_TABLES),
                    "priv_select": SELECT_RIGHT,
                    "priv_insert": INSERT_RIGHT,
                    "priv_update": UPDATE_RIGHT,
                    "priv_delete": DELETE_RIGHT,
                    "priv_truncate": TRUNCATE_RIGHT,
                },
            )
        failed = _failed_checks(role_rows, table_rows)
    except Exception as exc:  # noqa: BLE001 - Absicht: Auch Unerwartetes darf keinen Text nach außen tragen
        _log_failure(exc)
        # from None: kein __cause__, der Kontext ist unterdrückt
        raise RoleCheckError((DATABASE_FAILURE,)) from None
    # Außerhalb des try: Das Ergebnis der Prüfung wird weder gefangen noch ein zweites Mal geloggt
    if failed:
        raise RoleCheckError(failed)
