"""Einstiegspunkt des Seed-Moduls: python -m db.seed (F05).

Hier, und nur hier, wird die Umgebung gelesen: DATABASE_URL (Ziel) und SEED_CONFIRM_RESET
(Bestätigung zum Überschreiben vorhandener Tabellen). Es gibt keine .env-Datei und keinen dotenv.

Rückgabecodes: 0 Erfolg, 1 Datenfehler (Loader, Prüfungen), 2 Abbruch durch den Löschschutz oder
die Umgebung, 3 Verbindungs- oder Datenbankfehler.

Option --nur-rabatte: erzeugt nur data/richtlinien/rabatte.md aus den JSON-Dateien, ohne
Datenbank und ohne DATABASE_URL.
"""

import argparse
import os
import sys
from collections.abc import Mapping, Sequence

import psycopg

from .guard import SeedGuardError, parse_target
from .loader import DEFAULT_DATA_DIR, SeedDataError
from .rabatte import render_rabatte, write_rabatte
from .writer import (
    DEFAULT_RICHTLINIEN_DIR,
    DEFAULT_SCHEMA_SQL,
    RABATTE_FILENAME,
    SeedWriteError,
    load_checked_data,
    run,
)

# Pfade als Modulwerte, damit Tests sie austauschen können
DATA_DIR = DEFAULT_DATA_DIR
SCHEMA_SQL = DEFAULT_SCHEMA_SQL
RICHTLINIEN_DIR = DEFAULT_RICHTLINIEN_DIR

USAGE_MESSAGE = "Ungültiger Aufruf. Erlaubt ist nur die Option --nur-rabatte."
CONNECT_MESSAGE = "Verbindung zur Datenbank fehlgeschlagen."


class _UsageError(Exception):
    pass


class _Parser(argparse.ArgumentParser):
    def error(self, message: str):
        raise _UsageError


def _parse_args(argv: Sequence[str] | None) -> argparse.Namespace:
    parser = _Parser(prog="python -m db.seed", add_help=False)
    parser.add_argument("--nur-rabatte", action="store_true", dest="nur_rabatte")
    return parser.parse_args(argv)


def _error(text: str) -> None:
    print(text, file=sys.stderr)


def _only_rabatte() -> int:
    try:
        data = load_checked_data(DATA_DIR)
    except SeedDataError as exc:
        _error(str(exc))
        return 1
    path = RICHTLINIEN_DIR / RABATTE_FILENAME
    failure = None
    try:
        write_rabatte(path, render_rabatte(data))
    except Exception as exc:
        failure = type(exc).__name__
    if failure is not None:
        _error(f"{RABATTE_FILENAME} konnte nicht geschrieben werden ({failure}).")
        return 3
    print(f"{RABATTE_FILENAME} geschrieben: {path}")
    return 0


def _connect(url: str) -> tuple[psycopg.Connection | None, str | None]:
    """Verbindet mit der unveränderten URL. Gibt (Verbindung, None) oder (None, Klassenname) zurück."""
    failure = None
    try:
        return psycopg.connect(url, autocommit=True, connect_timeout=10), None
    except Exception as exc:
        failure = type(exc).__name__
    return None, failure


def main(argv: Sequence[str] | None = None, environ: Mapping[str, str] | None = None) -> int:
    env = os.environ if environ is None else environ
    usage_error = False
    args = None
    try:
        args = _parse_args(argv)
    except _UsageError:
        usage_error = True
    if usage_error or args is None:
        _error(USAGE_MESSAGE)
        return 2
    if args.nur_rabatte:
        return _only_rabatte()

    url = env.get("DATABASE_URL")
    target = None
    try:
        target = parse_target(url, env)
    except SeedGuardError as exc:
        _error(str(exc))
        return 2
    try:
        load_checked_data(DATA_DIR)
    except SeedDataError as exc:
        _error(str(exc))
        return 1

    connection, failure = _connect(url or "")
    if connection is None:
        _error(f"{CONNECT_MESSAGE} ({failure})")
        return 3
    try:
        counts = run(
            connection,
            target,
            DATA_DIR,
            SCHEMA_SQL,
            RICHTLINIEN_DIR,
            env.get("SEED_CONFIRM_RESET"),
            print,
        )
    except SeedGuardError as exc:
        _error(str(exc))
        return 2
    except SeedDataError as exc:
        _error(str(exc))
        return 1
    except SeedWriteError as exc:
        _error(str(exc))
        return 3
    finally:
        connection.close()
    print("Fertig. Zeilen je Tabelle:")
    for name, count in counts.items():
        print(f"  {name}: {count}")
    print(f"{RABATTE_FILENAME} wurde erzeugt.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
