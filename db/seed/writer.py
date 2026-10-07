"""Schreiben der Stammdaten in die Datenbank (F05, Quelle: docs/DATENMODELL.md Abschnitt 4).

run() lädt und prüft die Daten, prüft den Löschschutz, löscht die sechs Seed-Tabellen, legt sie
mit db/schema.sql neu an und füllt sie, alles in EINER Transaktion. Danach (und nur dann) wird
data/richtlinien/rabatte.md erzeugt.

Die Verbindung muss im Autocommit-Modus laufen: Dann ist conn.transaction() eine echte
Transaktion mit Commit. In den Tests läuft run() unter einer äußeren Transaktion, dort wird
daraus ein Savepoint. Es gibt nie conn.commit().

Fehler der Datenbank werden als feste deutsche Meldung gemeldet: Klassenname der Ausnahme,
SQLSTATE und Name des Constraints. Der Text der Bibliotheksmeldung und die URL kommen nie vor.
Die Meldung wird außerhalb des except-Blocks geworfen, damit kein Kontext mit Bibliothekstext
mitläuft. SeedGuardError und SeedDataError werden unverändert durchgereicht.
"""

import json
import re
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from decimal import Decimal
from pathlib import Path
from typing import Any

import psycopg
from psycopg import sql
from psycopg.types.json import Jsonb

from .checks import run_all_checks
from .guard import (
    RESET_TABLES,
    SeedGuardError,
    Target,
    check_reset_confirmation,
    describe_target,
    verify_effective_host,
)
from .loader import SeedData, SeedDataError, load_seed_data
from .rabatte import render_rabatte, write_rabatte

DEFAULT_SCHEMA_SQL = Path(__file__).resolve().parents[1] / "schema.sql"
DEFAULT_RICHTLINIEN_DIR = Path(__file__).resolve().parents[2] / "data" / "richtlinien"
RABATTE_FILENAME = "rabatte.md"

AUTOCOMMIT_MESSAGE = "Die Verbindung muss im Autocommit-Modus laufen."
NO_SCHEMA_MESSAGE = "Die Verbindung hat kein aktuelles Schema."
DEPENDENT_MESSAGE = "Abhängige Objekte verhindern das Löschen. Es wurde nichts verändert."

_SAFE_NAME = re.compile(r"[A-Za-z0-9_]{1,63}")
_SQLSTATE = re.compile(r"[0-9A-Z]{5}")


class SeedWriteError(Exception):
    """Schreiben oder Lesen in der Datenbank ist fehlgeschlagen. Feste Meldung ohne Bibliothekstext."""


def json_dumps_exact(value: Any) -> str:
    """JSON-Text, in dem Decimal als Zahl steht (nie als Text, nie über float)."""
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, int):
        return str(value)
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError("Keine endliche Zahl")
        return format(value, "f")
    if isinstance(value, str):
        return json.dumps(value)
    if isinstance(value, list | tuple):
        return "[" + ",".join(json_dumps_exact(item) for item in value) + "]"
    if isinstance(value, dict):
        pairs = (json.dumps(str(key)) + ":" + json_dumps_exact(item) for key, item in value.items())
        return "{" + ",".join(pairs) + "}"
    raise TypeError(f"Nicht serialisierbarer Typ {type(value).__name__}")


def load_checked_data(data_dir: Path) -> SeedData:
    """Lädt die Stammdaten und führt alle Prüfungen aus. Wirft SeedDataError mit allen Meldungen."""
    data = load_seed_data(data_dir)
    messages = run_all_checks(data)
    if messages:
        raise SeedDataError(messages)
    return data


# Fehlerweg


@dataclass(frozen=True, slots=True)
class _Failure:
    kind: str
    sqlstate: str | None = None
    constraint: str | None = None


def _failure_from(exc: BaseException) -> _Failure:
    """Nur Klassenname, SQLSTATE und Constraint-Name, nie der Text der Ausnahme."""
    kind = type(exc).__name__
    if not kind.isascii():
        kind = "Exception"
    sqlstate = None
    constraint = None
    if isinstance(exc, psycopg.Error):
        raw_state = exc.sqlstate
        if isinstance(raw_state, str) and _SQLSTATE.fullmatch(raw_state):
            sqlstate = raw_state
        raw_name = exc.diag.constraint_name
        if isinstance(raw_name, str) and _SAFE_NAME.fullmatch(raw_name):
            constraint = raw_name
    return _Failure(kind, sqlstate, constraint)


def _guarded[T](action: Callable[[], T]) -> tuple[T | None, _Failure | None]:
    """Führt action aus. Gibt (Ergebnis, None) oder (None, Fehler) zurück, wirft selbst nie.

    SeedGuardError, SeedDataError und SeedWriteError werden unverändert weitergereicht.
    """
    try:
        return action(), None
    except (SeedGuardError, SeedDataError, SeedWriteError):
        raise
    except Exception as exc:
        return None, _failure_from(exc)


def _describe_failure(prefix: str, failure: _Failure) -> str:
    parts = [failure.kind]
    if failure.sqlstate:
        parts.append(f"SQLSTATE {failure.sqlstate}")
    if failure.constraint:
        parts.append(f'Constraint "{failure.constraint}"')
    return f"{prefix} ({', '.join(parts)}). Die Datenbank wurde nicht verändert."


# Lesen des Zustands


def _inspect(conn: psycopg.Connection) -> tuple[str, dict[str, int]]:
    """Gibt (aktuelles Schema, vorhandene Seed-Tabellen mit Zeilenzahl) zurück."""
    row = conn.execute("SELECT current_schema()").fetchone()
    schema = row[0] if row else None
    if not isinstance(schema, str):
        raise SeedWriteError(NO_SCHEMA_MESSAGE)
    existing: dict[str, int] = {}
    for name in RESET_TABLES:
        found = conn.execute(
            "SELECT to_regclass(quote_ident(current_schema()) || '.' || quote_ident(%s)) IS NOT NULL",
            (name,),
        ).fetchone()
        if found and found[0]:
            query = sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(schema, name))
            counted = conn.execute(query).fetchone()
            existing[name] = int(counted[0]) if counted else 0
    return schema, existing


# Einfügen


def _insert_query(
    schema: str, table: str, columns: Sequence[str], returning: bool = False
) -> sql.Composable:
    query = sql.SQL("INSERT INTO {table} ({columns}) VALUES ({values})").format(
        table=sql.Identifier(schema, table),
        columns=sql.SQL(", ").join(sql.Identifier(column) for column in columns),
        values=sql.SQL(", ").join(sql.Placeholder() for _ in columns),
    )
    if returning:
        query = query + sql.SQL(" RETURNING id")
    return query


def _returned_id(cur: psycopg.Cursor) -> int:
    row = cur.fetchone()
    if row is None:
        raise RuntimeError("Keine ID zurückgegeben")
    return int(row[0])


def _insert_products(cur: psycopg.Cursor, schema: str, data: SeedData) -> dict[str, int]:
    columns = (
        "article_number",
        "name",
        "category",
        "description",
        "technical_data",
        "list_price",
        "price_unit",
        "lead_time_days",
        "is_active",
    )
    query = _insert_query(schema, "products", columns, returning=True)
    ids: dict[str, int] = {}
    for product in data.products:
        cur.execute(
            query,
            (
                product.article_number,
                product.name,
                product.category,
                product.description,
                Jsonb(product.technical_data, dumps=json_dumps_exact),
                product.list_price,
                product.price_unit,
                product.lead_time_days,
                product.is_active,
            ),
        )
        ids[product.article_number] = _returned_id(cur)
    return ids


def _insert_customers(cur: psycopg.Cursor, schema: str, data: SeedData) -> dict[str, int]:
    columns = ("company_name", "domain", "industry", "country", "status", "created_at")
    query = _insert_query(schema, "customers", columns, returning=True)
    ids: dict[str, int] = {}
    for customer in data.customers:
        cur.execute(
            query,
            (
                customer.company_name,
                customer.domain,
                customer.industry,
                customer.country,
                customer.status,
                customer.created_at,
            ),
        )
        ids[customer.domain] = _returned_id(cur)
    return ids


def _insert_contacts(
    cur: psycopg.Cursor, schema: str, data: SeedData, customer_ids: Mapping[str, int]
) -> dict[str, int]:
    columns = (
        "customer_id",
        "first_name",
        "last_name",
        "email",
        "job_title",
        "language",
        "created_at",
    )
    query = _insert_query(schema, "contacts", columns, returning=True)
    ids: dict[str, int] = {}
    for contact in data.contacts:
        cur.execute(
            query,
            (
                customer_ids[contact.customer_domain],
                contact.first_name,
                contact.last_name,
                contact.email,
                contact.job_title,
                contact.language,
                contact.created_at,
            ),
        )
        ids[contact.email] = _returned_id(cur)
    return ids


def _insert_discount_rules(cur: psycopg.Cursor, schema: str, data: SeedData) -> None:
    columns = (
        "customer_status",
        "product_category",
        "min_quantity",
        "max_discount_percent",
        "description",
    )
    query = _insert_query(schema, "discount_rules", columns)
    cur.executemany(
        query,
        [
            (
                rule.customer_status,
                rule.product_category,
                rule.min_quantity,
                rule.max_discount_percent,
                rule.description,
            )
            for rule in data.discount_rules
        ],
    )


def _insert_product_fits(
    cur: psycopg.Cursor, schema: str, data: SeedData, product_ids: Mapping[str, int]
) -> None:
    query = _insert_query(schema, "product_fits", ("product_id", "fits_product_id", "note"))
    cur.executemany(
        query,
        [(product_ids[fit.part], product_ids[fit.fits], fit.note) for fit in data.product_fits],
    )


def _insert_activities(
    cur: psycopg.Cursor,
    schema: str,
    data: SeedData,
    customer_ids: Mapping[str, int],
    contact_ids: Mapping[str, int],
    product_ids: Mapping[str, int],
) -> None:
    columns = (
        "customer_id",
        "contact_id",
        "product_id",
        "type",
        "occurred_at",
        "subject",
        "summary",
        "amount_eur",
        "created_by",
    )
    query = _insert_query(schema, "activities", columns)
    rows = []
    for activity in data.activities:
        contact_id = None
        if activity.contact_email is not None:
            contact_id = contact_ids[activity.contact_email]
        product_id = None
        if activity.article_number is not None:
            product_id = product_ids[activity.article_number]
        rows.append(
            (
                customer_ids[activity.customer_domain],
                contact_id,
                product_id,
                activity.type,
                activity.occurred_at,
                activity.subject,
                activity.summary,
                activity.amount_eur,
                activity.created_by,
            )
        )
    cur.executemany(query, rows)


def _write(
    conn: psycopg.Connection, schema: str, schema_sql: str, data: SeedData
) -> dict[str, int]:
    """Löschen, Neuanlegen und Befüllen in einer Transaktion (bei Fehler Rollback)."""
    with conn.transaction():
        for name in RESET_TABLES:
            conn.execute(sql.SQL("DROP TABLE IF EXISTS {}").format(sql.Identifier(schema, name)))
        conn.execute(schema_sql)
        with conn.cursor() as cur:
            product_ids = _insert_products(cur, schema, data)
            customer_ids = _insert_customers(cur, schema, data)
            contact_ids = _insert_contacts(cur, schema, data, customer_ids)
            _insert_discount_rules(cur, schema, data)
            _insert_product_fits(cur, schema, data, product_ids)
            _insert_activities(cur, schema, data, customer_ids, contact_ids, product_ids)
    return {
        "products": len(data.products),
        "customers": len(data.customers),
        "contacts": len(data.contacts),
        "discount_rules": len(data.discount_rules),
        "product_fits": len(data.product_fits),
        "activities": len(data.activities),
    }


def _read_schema_sql(path: Path) -> str:
    failure = None
    try:
        return Path(path).read_text(encoding="utf-8")
    except Exception as exc:
        failure = _failure_from(exc).kind
    raise SeedWriteError(f"db/schema.sql konnte nicht gelesen werden ({failure}).")


def _in_transaction_or_autocommit(conn: psycopg.Connection) -> bool:
    if conn.autocommit:
        return True
    return conn.info.transaction_status != psycopg.pq.TransactionStatus.IDLE


def run(
    conn: psycopg.Connection,
    target: Target,
    data_dir: Path,
    schema_sql_path: Path,
    richtlinien_dir: Path,
    confirm_value: str | None,
    out: Callable[[str], None],
) -> dict[str, int]:
    """Befüllt die Datenbank und gibt die Zeilenzahlen je Tabelle zurück.

    Reihenfolge: Daten laden und prüfen (ohne Datenbankzugriff), Host der Verbindung prüfen,
    vorhandene Tabellen ermitteln, Ausgabe und Bestätigung, dann eine Transaktion. Nach deren
    Erfolg wird rabatte.md in richtlinien_dir erzeugt.
    """
    data = load_checked_data(data_dir)
    schema_sql = _read_schema_sql(schema_sql_path)
    if not _in_transaction_or_autocommit(conn):
        raise SeedWriteError(AUTOCOMMIT_MESSAGE)
    verify_effective_host(target, conn.info.host)

    inspected, failure = _guarded(lambda: _inspect(conn))
    if failure is not None or inspected is None:
        raise SeedWriteError(
            _describe_failure(
                "Lesen der vorhandenen Tabellen fehlgeschlagen", failure or _Failure("Fehler")
            )
        )
    schema, existing = inspected

    out(describe_target(target, schema, existing))
    check_reset_confirmation(target, confirm_value, existing)

    counts, failure = _guarded(lambda: _write(conn, schema, schema_sql, data))
    if failure is not None or counts is None:
        failure = failure or _Failure("Fehler")
        if failure.sqlstate == "2BP01":
            raise SeedWriteError(DEPENDENT_MESSAGE)
        raise SeedWriteError(
            _describe_failure("Schreiben in die Datenbank fehlgeschlagen", failure)
        )

    text = render_rabatte(data)
    _, file_failure = _guarded(
        lambda: write_rabatte(Path(richtlinien_dir) / RABATTE_FILENAME, text)
    )
    if file_failure is not None:
        raise SeedWriteError(
            f"{RABATTE_FILENAME} konnte nicht geschrieben werden ({file_failure.kind}). "
            "Die Datenbank wurde befüllt."
        )
    return counts
