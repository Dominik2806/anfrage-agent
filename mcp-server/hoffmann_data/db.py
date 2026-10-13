"""Datenbankzugriff des Datenservice hoffmann-data (F07).

Nur dieses Modul importiert den Datenbanktreiber (psycopg); alle anderen Module bekommen die Verbindung
von hier (Test: test_mcp_no_leak.py). Der Lesepfad liest nur, der Schreibpfad legt nur an (Auftrag 5.1, ADR 0005).
Gemeinsam: Jeder Aufruf öffnet eine eigene Verbindung (kein Pool) und schließt sie am Ende, mit einem Zeitlimit von
5 Sekunden je Abfrage und prepare_threshold=None (keine serverseitig vorbereiteten Abfragen, Pooler im
Transaktionsmodus). Fehler der Datenbank werden auf eine feste Meldung abgebildet. Meldung, Log und Ausnahmekette
enthalten weder Host noch Benutzer noch Datenbankname noch URL noch den Text der Ausnahme.
- Lesepfad (Database, read_connection): Die Verbindung ist schreibgeschützt (read_only), es wird nie committet.
- Schreibpfad (WriteDatabase, write_connection): Die Verbindung ist nicht schreibgeschützt. Eine Transaktion je
  Aufruf: Commit am Ende, bei jeder Ausnahme Rollback, danach wird die Verbindung geschlossen. INSERT INTO steht nur
  in diesem Modul.
- Beim Start prüfen verify_read_only_role und verify_write_role, dass die Rolle der jeweiligen Verbindung wirklich nur
  lesen beziehungsweise nur lesen und anlegen darf (Etappe 7, F08). Die Prüfungen sind eine Momentaufnahme; die Rollen
  (db/roles/data_service_ro.sql, db/roles/data_service_rw.sql) bleiben die eigentliche Sperre.
"""

import logging
from collections.abc import Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager, suppress
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


# Startprüfung der Schreibrolle (F08, ADR 0005)

# Die Schreibrolle (db/roles/data_service_rw.sql) darf auf den ersten drei Tabellen genau lesen und anlegen,
# auf den letzten drei nichts. Die Reihenfolge ist die der Abfrage und der Meldung tabelle-fehlt.
WRITABLE_TABLES = ("customers", "contacts", "activities")
FORBIDDEN_TABLES = ("products", "product_fits", "discount_rules")
WRITE_CHECKED_TABLES = WRITABLE_TABLES + FORBIDDEN_TABLES
# Unerlaubte Rechte: (Recht, Spalte aus WRITE_TABLES_SQL), in der Reihenfolge der Ausgabe. Auf den
# Schreibtabellen ist INSERT erlaubt (und Pflicht), auf den Fremdtabellen ist jedes Recht unerlaubt.
_EXTRA_WRITE_CHECKS = (
    (UPDATE_RIGHT, "any_update"),
    (DELETE_RIGHT, "can_delete"),
    (TRUNCATE_RIGHT, "can_truncate"),
)
_FORBIDDEN_WRITE_CHECKS = ((INSERT_RIGHT, "any_insert"), *_EXTRA_WRITE_CHECKS)

# Eine Zeile je Tabelle in der Reihenfolge von WRITE_CHECKED_TABLES. Pflichtrechte (can_select, can_insert) zählen
# nur als Tabellenrecht, unerlaubte Rechte (any_*) auch als Spaltenrecht. Die Rechtenamen kommen nur als Parameter.
WRITE_TABLES_SQL = """SELECT
    t.table_name,
    to_regclass(t.table_name) IS NOT NULL AS present,
    COALESCE(
        has_table_privilege(current_user, to_regclass(t.table_name), %(priv_select)s::text), false
    ) AS can_select,
    COALESCE(
        has_any_column_privilege(current_user, to_regclass(t.table_name), %(priv_select)s::text), false
    ) AS any_select,
    COALESCE(
        has_table_privilege(current_user, to_regclass(t.table_name), %(priv_insert)s::text), false
    ) AS can_insert,
    COALESCE(
        has_any_column_privilege(current_user, to_regclass(t.table_name), %(priv_insert)s::text), false
    ) AS any_insert,
    COALESCE(
        has_any_column_privilege(current_user, to_regclass(t.table_name), %(priv_update)s::text), false
    ) AS any_update,
    COALESCE(
        has_table_privilege(current_user, to_regclass(t.table_name), %(priv_delete)s::text), false
    ) AS can_delete,
    COALESCE(
        has_table_privilege(current_user, to_regclass(t.table_name), %(priv_truncate)s::text), false
    ) AS can_truncate
FROM unnest(%(tables)s::text[]) WITH ORDINALITY AS t(table_name, ord)
ORDER BY t.ord"""


def _failed_write_checks(
    role_rows: list[dict[str, Any]], table_rows: list[dict[str, Any]]
) -> list[str]:
    """Die Kurzbezeichnungen der gescheiterten Prüfungen der Schreibrolle in fester Reihenfolge.

    1 Superuser, 2 BYPASSRLS, 3 unerlaubte Rechte auf den Schreibtabellen, 4 jedes Recht auf den Fremdtabellen
    (zuerst Lesen, dann Schreibrechte), 5 fehlendes SELECT, danach fehlendes INSERT auf den Schreibtabellen,
    6 CREATE im Schema, 7 fehlende Tabellen. Eine fehlende Tabelle erscheint nur unter 7.
    """
    role = role_rows[0]
    rows = {row["table_name"]: row for row in table_rows}
    writable = [rows[name] for name in WRITABLE_TABLES if rows[name]["present"]]
    forbidden = [rows[name] for name in FORBIDDEN_TABLES if rows[name]["present"]]
    failed: list[str] = []
    if role["rolsuper"]:
        failed.append(SUPERUSER_FAILURE)
    if role["rolbypassrls"]:
        failed.append(BYPASS_RLS_FAILURE)
    for row in writable:
        for right, column in _EXTRA_WRITE_CHECKS:
            if row[column]:
                failed.append(f"schreibrecht:{right}:{row['table_name']}")
    for row in forbidden:
        if row["any_select"]:
            failed.append(f"lesen-unerlaubt:{row['table_name']}")
        for right, column in _FORBIDDEN_WRITE_CHECKS:
            if row[column]:
                failed.append(f"schreibrecht:{right}:{row['table_name']}")
    for row in writable:
        if not row["can_select"]:
            failed.append(NO_SELECT_FAILURES[row["table_name"]])
    for row in writable:
        if not row["can_insert"]:
            failed.append(f"fehlendes-recht:{INSERT_RIGHT}:{row['table_name']}")
    if role["can_create"]:
        failed.append(CREATE_FAILURE)
    for row in table_rows:
        if not row["present"]:
            failed.append(f"tabelle-fehlt:{row['table_name']}")
    return failed


def verify_write_role(database: ConnectionSource) -> None:
    """Prüft beim Start, dass die Rolle der Verbindung nur anlegen und lesen darf. Wirft RoleCheckError, sonst None.

    Prüfungen: 1 kein Superuser, 2 kein BYPASSRLS, 3 auf customers, contacts, activities kein UPDATE, DELETE,
    TRUNCATE (UPDATE auch nicht als Spaltenrecht), 4 auf products, product_fits, discount_rules gar kein Recht
    (auch nicht als Spaltenrecht), 5 SELECT und INSERT auf den drei Schreibtabellen, 6 kein CREATE im aktuellen
    Schema, 7 alle sechs Tabellen existieren. Geprüft werden nur Rechte, nicht die Bedingungen der Policies.
    Nur lesende Abfragen, genau eine Verbindung. Scheitert die Verbindung oder eine Abfrage, steht im Fehler nur
    die Kurzbezeichnung `datenbankfehler`; geloggt wird genau einmal Klasse und SQLSTATE.
    """
    try:
        with database.connection() as connection:
            role_rows = fetch_all(connection, ROLE_SQL, {"priv_create": CREATE_RIGHT})
            table_rows = fetch_all(
                connection,
                WRITE_TABLES_SQL,
                {
                    "tables": list(WRITE_CHECKED_TABLES),
                    "priv_select": SELECT_RIGHT,
                    "priv_insert": INSERT_RIGHT,
                    "priv_update": UPDATE_RIGHT,
                    "priv_delete": DELETE_RIGHT,
                    "priv_truncate": TRUNCATE_RIGHT,
                },
            )
        failed = _failed_write_checks(role_rows, table_rows)
    except Exception as exc:  # noqa: BLE001 - Absicht: Auch Unerwartetes darf keinen Text nach außen tragen
        _log_failure(exc)
        # from None: kein __cause__, der Kontext ist unterdrückt
        raise RoleCheckError((DATABASE_FAILURE,)) from None
    # Außerhalb des try: Das Ergebnis der Prüfung wird weder gefangen noch ein zweites Mal geloggt
    if failed:
        raise RoleCheckError(failed)


# Schreibweg (F08 Etappe 2, ADR 0005 Punkt 8)

DUPLICATE_ERROR = "Der Eintrag existiert bereits."


def _rollback_quietly(connection: Any) -> None:
    """Rollt zurück und verschluckt einen Fehler dabei: Er darf die ursprüngliche Ausnahme nicht verdecken."""
    with suppress(Exception):
        connection.rollback()


class WriteDatabase:
    """Verbindungsquelle für den Schreibweg. Der Konstruktor verbindet nicht.

    Eigenständig und getrennt von Database (der Lesepfad bleibt unberührt). Jeder Aufruf ist eine Transaktion
    auf einer eigenen Verbindung (kein Pool): Das Ende des Blocks ohne Ausnahme ist der Commit, jede Ausnahme im
    Block ist ein Rollback. Die Verbindung ist nicht schreibgeschützt und hat ein Zeitlimit von 5 Sekunden je
    Abfrage.
    """

    def __init__(self, url: str, **connect_kwargs: Any) -> None:
        self._url = url
        self._connect_kwargs = connect_kwargs

    def __repr__(self) -> str:
        # Keine URL: Sie enthält Benutzer, Passwort und Host
        return "WriteDatabase()"

    @contextmanager
    def connection(self) -> Iterator[psycopg.Connection[Any]]:
        """Öffnet eine eigene Verbindung, committet am Ende, rollt bei jeder Ausnahme zurück und schließt sie."""
        # Unsere Werte stehen zuletzt und gewinnen gegen connect_kwargs und gegen Parameter in der URL
        options = {
            **self._connect_kwargs,
            "connect_timeout": CONNECT_TIMEOUT_SECONDS,
            "prepare_threshold": None,
        }
        # Scheitert das Verbinden, gibt es nichts zu schließen
        connection = psycopg.connect(self._url, **options)
        try:
            # Beginnt die Transaktion; SET LOCAL gilt bis zum Commit oder Rollback dieses einen Aufrufs
            connection.execute(STATEMENT_TIMEOUT_SQL)
            yield connection
            # Im try: Scheitert der Commit, rollt der Zweig unten zurück
            connection.commit()
        except BaseException:
            _rollback_quietly(connection)
            raise
        finally:
            connection.close()


@contextmanager
def write_connection(database: ConnectionSource | None) -> Iterator[Any]:
    """Verbindung für einen schreibenden Werkzeugaufruf (Gegenstück zu read_connection).

    - Ohne Datenbank: ToolError(NOT_CONFIGURED), nie Scheindaten.
    - Ein ToolError aus dem Block (z. B. ungültige Eingabe) läuft unverändert durch; die Datenbank hat ihn
      gesehen und zurückgerollt.
    - Eine UniqueViolation (SQLSTATE 23505) wird zu ToolError(DUPLICATE_ERROR).
    - Jeder andere Fehler (psycopg.Error, unerwartete Ausnahme, auch beim Verbinden) wird zu
      ToolError(DATABASE_ERROR). Beides wird einmal geloggt (Klasse und SQLSTATE, nie der Text) und ohne Ursache
      weitergegeben.
    """
    if database is None:
        raise ToolError(NOT_CONFIGURED)
    try:
        with database.connection() as connection:
            yield connection
    except ToolError:
        raise
    except psycopg.errors.UniqueViolation as exc:
        _log_failure(exc)
        raise ToolError(DUPLICATE_ERROR) from None
    except Exception as exc:  # noqa: BLE001 - Absicht: Auch unerwartete Ausnahmen dürfen keinen Text nach außen tragen
        _log_failure(exc)
        # from None: kein __cause__ und unterdrückter Kontext, damit der Text der Ausnahme nirgends mitläuft
        raise ToolError(DATABASE_ERROR) from None


# log_activity (F08 Etappe 4b, ADR 0005 Punkt 7). Alle INSERT INTO des Pakets stehen in db.py. Alle Werte sind benannte
# Parameter; fest stehen created_by ('agent', die INSERT-Policy der Schreibrolle verlangt es) und occurred_at (now() der
# Datenbank: die Tabelle hat dafür keinen Default). Kein amount_eur, kein product_id.
INSERT_ACTIVITY_SQL = """INSERT INTO activities
    (customer_id, contact_id, type, occurred_at, subject, summary, created_by)
VALUES
    (%(customer_id)s::bigint, %(contact_id)s::bigint, %(activity_type)s::text, now(),
     %(subject)s::text, %(summary)s::text, 'agent')"""

# domain und email sind UNIQUE: höchstens eine Zeile. Der Kontakt wird immer zusammen mit dem Kunden gesucht.
FIND_CUSTOMER_ID_SQL = """SELECT id FROM customers WHERE domain = %(domain)s::text"""

FIND_CONTACT_ID_SQL = """SELECT id FROM contacts
WHERE email = %(email)s::text AND customer_id = %(customer_id)s::bigint"""


def find_customer_id(connection: Any, domain: str) -> int | None:
    """Die interne id des Kunden mit dieser Domain oder None. Nur intern, nie Teil einer Ausgabe."""
    rows = fetch_all(connection, FIND_CUSTOMER_ID_SQL, {"domain": domain})
    return int(rows[0]["id"]) if rows else None


def find_contact_id(connection: Any, customer_id: int, email: str) -> int | None:
    """Die interne id des Kontakts mit dieser Adresse bei genau diesem Kunden oder None."""
    rows = fetch_all(connection, FIND_CONTACT_ID_SQL, {"email": email, "customer_id": customer_id})
    return int(rows[0]["id"]) if rows else None


def insert_activity(
    connection: Any,
    customer_id: int,
    contact_id: int | None,
    activity_type: str,
    subject: str,
    summary: str,
) -> None:
    """Legt genau eine Aktivität an: created_by fest 'agent', occurred_at = now() der Datenbank. Gibt nichts zurück."""
    connection.execute(
        INSERT_ACTIVITY_SQL,
        {
            "customer_id": customer_id,
            "contact_id": contact_id,
            "activity_type": activity_type,
            "subject": subject,
            "summary": summary,
        },
    )


# create_lead (F08 Etappe 4c, ADR 0005 Punkt 5). Jeder INSERT steht in einer eigenen Zeichenkette. Alle Werte sind
# benannte Parameter; der Status steht fest im Text ('lead', die INSERT-Policy der Schreibrolle verlangt es), created_at
# setzt der Standardwert der Tabelle (now()).
# Die drei Nachschlagungen sind EINE Anweisung: In READ COMMITTED sieht jede Anweisung den Stand zu ihrem eigenen
# Zeitpunkt. Mit drei getrennten Abfragen könnte ein paralleler Aufruf zwischen ihnen committen, und aus "kein Treffer
# über die Domain, Treffer über den Namen" würde ein falscher Konflikt. Eine Anweisung ist eine Momentaufnahme. Jede
# Unterabfrage liefert höchstens eine Zeile (domain, lower(company_name) und email sind eindeutig). Der Firmenname wird
# wie der eindeutige Index auf lower(company_name) gesucht.
FIND_LEAD_MATCHES_SQL = """SELECT
    (SELECT id FROM customers WHERE domain = %(domain)s::text) AS domain_customer_id,
    (SELECT id FROM customers WHERE lower(company_name) = lower(%(company_name)s::text)) AS name_customer_id,
    (SELECT customer_id FROM contacts WHERE email = %(email)s::text) AS contact_customer_id"""

INSERT_CUSTOMER_SQL = """INSERT INTO customers
    (company_name, domain, industry, country, status)
VALUES
    (%(company_name)s::text, %(domain)s::text, %(industry)s::text, %(country)s::text, 'lead')
RETURNING id"""

INSERT_CONTACT_SQL = """INSERT INTO contacts
    (customer_id, first_name, last_name, email, job_title, language)
VALUES
    (%(customer_id)s::bigint, %(first_name)s::text, %(last_name)s::text, %(email)s::text,
     %(job_title)s::text, %(language)s::text)"""


def find_lead_matches(
    connection: Any, domain: str, company_name: str, email: str
) -> tuple[int | None, int | None, int | None]:
    """(Kunde der Domain, Kunde des Firmennamens, Kunde der Adresse), je None ohne Treffer. Nur intern.

    Der Firmenname wird ohne Beachtung der Schreibung gesucht. Eine Anweisung, also ein Stand für alle drei.
    """
    rows = fetch_all(
        connection,
        FIND_LEAD_MATCHES_SQL,
        {"domain": domain, "company_name": company_name, "email": email},
    )
    row = rows[0]  # ein SELECT ohne FROM liefert immer genau eine Zeile
    by_domain, by_name, owner = (
        row["domain_customer_id"],
        row["name_customer_id"],
        row["contact_customer_id"],
    )
    return (
        None if by_domain is None else int(by_domain),
        None if by_name is None else int(by_name),
        None if owner is None else int(owner),
    )


def insert_customer(
    connection: Any, company_name: str, domain: str, industry: str, country: str
) -> int:
    """Legt genau einen Kunden mit Status lead an und gibt seine interne id zurück (nur intern, nie ausgeben)."""
    with connection.cursor(row_factory=dict_row) as cursor:
        cursor.execute(
            INSERT_CUSTOMER_SQL,
            {
                "company_name": company_name,
                "domain": domain,
                "industry": industry,
                "country": country,
            },
        )
        row = cursor.fetchone()
    assert row is not None  # RETURNING id liefert bei Erfolg immer genau eine Zeile
    return int(row["id"])


def insert_contact(
    connection: Any,
    customer_id: int,
    first_name: str,
    last_name: str,
    email: str,
    job_title: str | None,
    language: str,
) -> None:
    """Legt genau einen Kontakt an einem bestehenden Kunden an. Gibt nichts zurück."""
    connection.execute(
        INSERT_CONTACT_SQL,
        {
            "customer_id": customer_id,
            "first_name": first_name,
            "last_name": last_name,
            "email": email,
            "job_title": job_title,
            "language": language,
        },
    )
