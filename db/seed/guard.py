"""Löschschutz für das Seed-Skript (F05, Quelle: docs/DATENMODELL.md Abschnitt 4).

Reine Funktionen ohne Datenbankzugriff: Sie bestimmen das Ziel aus der Datenbank-URL, prüfen
den tatsächlich verbundenen Host, verlangen vor dem Löschen vorhandener Daten die Bestätigung
SEED_CONFIRM_RESET und liefern den Ausgabetext sowie die festen DROP-Befehle.

Die Umgebung wird hier nie gelesen. Umgebungswerte kommen als Parameter (Mapping) herein; den
Zugriff darauf macht erst __main__.

Geheimnisse: Keine Meldung und kein repr einer Ausnahme enthält die URL, den Benutzer, das
Passwort oder den übergebenen Bestätigungswert. Meldungen bestehen aus festen Texten und
nennen höchstens Host, Datenbankname, Schema, Tabellennamen, Zeilenzahlen sowie die Namen von
Parametern und Umgebungsvariablen. Ausnahmen werden außerhalb von except-Blöcken geworfen, damit
keine Meldung einer Bibliotheksausnahme als Kontext mitläuft.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlsplit

CONFIRM_VARIABLE = "SEED_CONFIRM_RESET"

# URL-Parameter, die den geprüften Host überstimmen würden
OVERRIDING_PARAMS = ("host", "hostaddr", "service")
# Umgebungsvariablen, die den geprüften Host überstimmen würden
OVERRIDING_ENV = ("PGHOSTADDR", "PGSERVICE")

# Die Seed-Tabellen in der Reihenfolge des Löschens (abhängige zuerst, Fremdschlüssel RESTRICT)
RESET_TABLES = (
    "activities",
    "product_fits",
    "contacts",
    "discount_rules",
    "customers",
    "products",
)


class SeedGuardError(Exception):
    """Der Löschschutz verbietet den Lauf. Die Meldung enthält keine Zugangsdaten."""


@dataclass(frozen=True, slots=True)
class Target:
    """Ziel der Befüllung. Enthält nur Host und Datenbankname, nie Zugangsdaten."""

    host: str
    dbname: str


_UNREADABLE = "DATABASE_URL ist keine lesbare URL."
_URL_PREFIXES = ("postgresql://", "postgres://")


def _parse(database_url: str | None, environ: Mapping[str, str]) -> Target | str:
    """Gibt das Ziel oder eine Fehlermeldung zurück (wirft selbst nie)."""
    if database_url is None or not database_url.strip():
        return "DATABASE_URL fehlt oder ist leer."
    # Wie libpq: Das Schema muss klein geschrieben sein, sonst ist es keine URL
    if not database_url.startswith(_URL_PREFIXES):
        return "DATABASE_URL muss mit postgresql:// beginnen."
    # urlsplit entfernt Leerzeichen und Zeilenumbrüche und ändert Unicode, libpq nicht
    if any(not 0x21 <= ord(char) <= 0x7E for char in database_url):
        return (
            "DATABASE_URL darf nur sichtbare ASCII-Zeichen enthalten (Leerzeichen, "
            "Steuerzeichen und Sonderzeichen prozentkodieren)."
        )
    try:
        parts = urlsplit(database_url)
    except ValueError:
        return _UNREADABLE
    if "#" in database_url:
        return (
            "DATABASE_URL enthält ein #. Sonderzeichen im Passwort müssen prozentkodiert "
            "werden, zum Beispiel %23."
        )
    # libpq sucht das @ der Anmeldung bis zum ersten /, urlsplit nur bis zum ersten / oder ?
    authority = database_url.partition("://")[2].partition("/")[0]
    if authority.count("@") > 1:
        return (
            "DATABASE_URL enthält mehr als ein @. "
            "Ein @ im Passwort muss als %40 geschrieben werden."
        )
    if "?" in authority.partition("@")[0] and "@" in authority:
        return (
            "DATABASE_URL hat einen unklaren Aufbau: Ein @ steht hinter einem ? vor dem ersten /."
        )
    # Ein Komma im Passwort ist erlaubt: nur der Teil nach dem letzten @ ist der Hostteil
    if "," in parts.netloc.rpartition("@")[2]:
        return "DATABASE_URL darf nur einen Host enthalten (kein Komma in der Adresse)."
    host = parts.hostname
    if not host:
        return "DATABASE_URL enthält keinen Host (Unix-Sockets sind nicht erlaubt)."
    # libpq dekodiert den Host, urlsplit nicht: Der geprüfte Host wäre ein anderer
    if "%" in host:
        return "DATABASE_URL darf im Host keine Prozentkodierung enthalten."
    try:
        _ = parts.port  # wirft ValueError bei ungültigem Port
    except ValueError:
        return _UNREADABLE
    query_names = {name.lower() for name in parse_qs(parts.query, keep_blank_values=True)}
    overriding = sorted(name for name in OVERRIDING_PARAMS if name in query_names)
    if overriding:
        return (
            f"DATABASE_URL darf die Parameter {', '.join(overriding)} nicht enthalten: "
            "Sie würden den geprüften Host überstimmen."
        )
    if "dbname" in query_names:
        return (
            "DATABASE_URL darf den Parameter dbname nicht enthalten: "
            "Er würde die angezeigte Datenbank überstimmen."
        )
    try:
        # strict: Ungültige UTF-8-Folgen würden sonst still ersetzt, libpq behält die Bytes
        dbname = unquote(parts.path.removeprefix("/"), errors="strict")
    except UnicodeDecodeError:
        return _UNREADABLE
    if not dbname:
        return "DATABASE_URL enthält keinen Datenbanknamen."
    set_names = [name for name in OVERRIDING_ENV if name in environ]
    if set_names:
        return (
            f"Die Umgebungsvariable(n) {', '.join(set_names)} würden den geprüften Host "
            "überstimmen und dürfen nicht gesetzt sein."
        )
    return Target(host=host.lower(), dbname=dbname)


def parse_target(database_url: str | None, environ: Mapping[str, str]) -> Target:
    """Bestimmt Host und Datenbankname aus der URL oder wirft SeedGuardError.

    Abgelehnt werden: fehlende oder leere URL, anderes oder groß geschriebenes Schema, andere
    Zeichen als sichtbares ASCII, # in der URL, mehr als ein @, mehrere Hosts, kein Host
    (Unix-Socket), Prozentkodierung im Host, unlesbarer Port, die Parameter host, hostaddr,
    service und dbname, ein fehlender Datenbankname sowie gesetzte Werte PGHOSTADDR oder
    PGSERVICE in environ. Wo urlsplit und libpq die URL verschieden zerlegen würden, wird die
    URL abgelehnt statt umgerechnet. Der Host wird kleingeschrieben, eine IPv6-Adresse ohne
    eckige Klammern geliefert.
    """
    result = _parse(database_url, environ)
    if isinstance(result, str):
        raise SeedGuardError(result)
    return result


def verify_effective_host(target: Target, effective_host: str | None) -> None:
    """Der Host der Verbindung muss dem Host aus der URL entsprechen (ohne Groß/Klein)."""
    if not effective_host:
        raise SeedGuardError(
            "Der tatsächlich verwendete Host der Verbindung ist unbekannt. "
            "Abbruch, es wurde nichts verändert."
        )
    # Der tatsächliche Host kommt von libpq und wird nie in die Meldung übernommen: Bei einem
    # nicht kodierten @ im Passwort könnte er Teile des Passworts enthalten.
    if effective_host.casefold() != target.host.casefold():
        raise SeedGuardError(
            "Die Verbindung geht an einen anderen Host als die URL "
            f'("{target.host}"). Abbruch, es wurde nichts verändert.'
        )


def check_reset_confirmation(
    target: Target, confirm_value: str | None, existing: Mapping[str, int]
) -> None:
    """Verlangt die Bestätigung, wenn Seed-Tabellen schon vorhanden sind.

    existing: Tabellenname -> Zeilenzahl der vorhandenen Seed-Tabellen. Eine leere Zuordnung
    (frische Datenbank) braucht keine Bestätigung, eine Tabelle mit 0 Zeilen schon. Der Wert
    muss dem Host ohne Beachtung der Groß-/Kleinschreibung genau entsprechen: ohne Entfernen
    von Leerzeichen, ohne Port, kein Teilstring, kein Präfix. Der Wert wird nie in die Meldung
    übernommen.
    """
    if not existing:
        return
    if isinstance(confirm_value, str) and confirm_value.casefold() == target.host.casefold():
        return
    raise SeedGuardError(
        f'Im Ziel "{target.host}" (Datenbank {target.dbname}) sind bereits Tabellen vorhanden. '
        f'Zum Überschreiben muss {CONFIRM_VARIABLE} genau auf den Host "{target.host}" '
        "gesetzt sein (nicht yes, ohne Port, ohne Leerzeichen). Es wurde nichts gelöscht."
    )


def _ordered_tables(existing: Mapping[str, int]) -> list[str]:
    known = [name for name in RESET_TABLES if name in existing]
    others = sorted(name for name in existing if name not in RESET_TABLES)
    return known + others


def describe_target(target: Target, schema: str, existing: Mapping[str, int]) -> str:
    """Mehrzeiliger Text für die Ausgabe vor dem Löschen. Enthält nie die URL."""
    lines = [
        "Ziel der Befüllung",
        f"  Host:      {target.host}",
        f"  Datenbank: {target.dbname}",
        f"  Schema:    {schema}",
    ]
    if not existing:
        lines.append("Keine vorhandenen Tabellen: Es wird nichts gelöscht.")
        return "\n".join(lines)
    lines.append("Vorhandene Tabellen:")
    for name in _ordered_tables(existing):
        count = existing[name]
        lines.append(f"  {name}: {count} {'Zeile' if count == 1 else 'Zeilen'}")
    lines.append(
        "Der Reset löscht diese Tabellen und legt sie neu an. Dabei gehen auch Zeilen "
        "verloren, die später vom Agenten und von Mitarbeitenden entstanden sind "
        "(created_by agent oder staff)."
    )
    return "\n".join(lines)


def drop_statements() -> list[str]:
    """Die sechs Löschbefehle in fester Reihenfolge, ohne CASCADE, nie DROP SCHEMA.

    Die Namen kommen nur aus RESET_TABLES. Die Befehle sind unqualifiziert; der Writer baut die
    ausgeführten Befehle aus RESET_TABLES mit dem Schema der Verbindung.
    """
    return [f"DROP TABLE IF EXISTS {name}" for name in RESET_TABLES]
