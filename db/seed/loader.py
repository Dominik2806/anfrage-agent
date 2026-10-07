"""Lädt die Stammdaten für das Seed-Skript (F05, Quelle: docs/DATENMODELL.md Abschnitt 4).

Liest die sechs JSON-Dateien aus einem Datenordner (UTF-8), prüft ihre Form streng und liefert
unveränderliche Datensätze. Streng heißt: Pflichtfelder müssen vorhanden sein, unbekannte Felder
(Tippfehler) werden abgelehnt, Grundtypen müssen stimmen, Zeitpunkte brauchen eine Zeitzone.
Alle Formfehler werden gesammelt und als eine SeedDataError gemeldet.

Das Modul liest keine Umgebungsvariablen, keine .env-Dateien und greift nie auf eine Datenbank
zu. Inhaltliche Prüfungen (Verweise, Kategorien, Zeiten) liegen in checks.py. Auswahlwerte und
Formate (z. B. Kategorien, .example) prüfen die CHECK-Constraints beim Schreiben.
"""

import json
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from pathlib import Path
from typing import Any

# Ordner mit den echten Stammdaten, relativ zu diesem Modul (db/seed/loader.py)
DEFAULT_DATA_DIR = Path(__file__).resolve().parents[2] / "data" / "stammdaten"


class SeedDataError(Exception):
    """Die Stammdaten sind fehlerhaft. Alle gefundenen Formfehler stehen in messages."""

    def __init__(self, messages: Iterable[str]) -> None:
        self.messages: tuple[str, ...] = tuple(messages)
        super().__init__("\n".join(self.messages))


# Datensätze. Das Feld index ist die laufende Nummer in der Datei (ab 1) für Fehlermeldungen.


@dataclass(frozen=True, slots=True)
class Product:
    index: int
    article_number: str
    name: str
    category: str
    description: str
    technical_data: dict[str, Any]
    list_price: Decimal
    price_unit: str
    lead_time_days: int
    is_active: bool


@dataclass(frozen=True, slots=True)
class ProductFit:
    index: int
    part: str
    fits: str
    note: str | None


@dataclass(frozen=True, slots=True)
class Customer:
    index: int
    company_name: str
    domain: str
    industry: str
    country: str
    status: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Contact:
    index: int
    customer_domain: str
    first_name: str
    last_name: str
    email: str
    job_title: str | None
    language: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class Activity:
    index: int
    customer_domain: str
    contact_email: str | None
    article_number: str | None
    type: str
    occurred_at: datetime
    subject: str
    summary: str
    amount_eur: Decimal | None
    created_by: str


@dataclass(frozen=True, slots=True)
class DiscountRule:
    index: int
    customer_status: str | None
    product_category: str | None
    min_quantity: int
    max_discount_percent: Decimal
    description: str


@dataclass(frozen=True, slots=True)
class SeedData:
    """Alle geladenen Stammdaten. Die Listen sind Tupel und damit nicht veränderbar."""

    products: tuple[Product, ...]
    product_fits: tuple[ProductFit, ...]
    customers: tuple[Customer, ...]
    contacts: tuple[Contact, ...]
    activities: tuple[Activity, ...]
    discount_rules: tuple[DiscountRule, ...]


# Beschreibung der Dateien


@dataclass(frozen=True, slots=True)
class Field:
    name: str
    # str, int, bool, decimal, object oder datetime
    kind: str
    # Der Schlüssel muss vorhanden sein, der Wert darf aber null sein
    nullable: bool = False
    # Der Schlüssel darf fehlen (dann None), der Wert darf null sein
    optional: bool = False


@dataclass(frozen=True, slots=True)
class FileSpec:
    filename: str
    record: type[Any]
    fields: tuple[Field, ...]
    # Kennzeichen eines Eintrags für Meldungen, aus den Rohdaten gebildet (kann leer sein)
    label: Callable[[dict[str, Any]], str]


def _label_from(*names: str, sep: str = ", ") -> Callable[[dict[str, Any]], str]:
    def label(entry: dict[str, Any]) -> str:
        parts = [
            str(entry[name])
            for name in names
            if isinstance(entry.get(name), str | int) and not isinstance(entry.get(name), bool)
        ]
        return sep.join(parts)

    return label


def rule_label(customer_status: Any, product_category: Any, min_quantity: Any) -> str:
    """Kennzeichen einer Rabattregel für Meldungen, z. B. "existing, conveyor, ab 10".

    Wird vom Loader (Rohdaten) und von checks.py (geladene Regeln) gemeinsam verwendet.
    """
    return f"{customer_status or 'alle'}, {product_category or 'alle'}, ab {min_quantity}"


def _rule_entry_label(entry: dict[str, Any]) -> str:
    return rule_label(
        entry.get("customer_status"), entry.get("product_category"), entry.get("min_quantity")
    )


FILES: dict[str, FileSpec] = {
    "products": FileSpec(
        "products.json",
        Product,
        (
            Field("article_number", "str"),
            Field("name", "str"),
            Field("category", "str"),
            Field("description", "str"),
            Field("technical_data", "object"),
            Field("list_price", "decimal"),
            Field("price_unit", "str"),
            Field("lead_time_days", "int"),
            Field("is_active", "bool"),
        ),
        _label_from("article_number"),
    ),
    "product_fits": FileSpec(
        "product_fits.json",
        ProductFit,
        (
            Field("part", "str"),
            Field("fits", "str"),
            Field("note", "str", optional=True),
        ),
        _label_from("part", "fits", sep=" / "),
    ),
    "customers": FileSpec(
        "customers.json",
        Customer,
        (
            Field("company_name", "str"),
            Field("domain", "str"),
            Field("industry", "str"),
            Field("country", "str"),
            Field("status", "str"),
            Field("created_at", "datetime"),
        ),
        _label_from("domain"),
    ),
    "contacts": FileSpec(
        "contacts.json",
        Contact,
        (
            Field("customer_domain", "str"),
            Field("first_name", "str"),
            Field("last_name", "str"),
            Field("email", "str"),
            Field("job_title", "str", optional=True),
            Field("language", "str"),
            Field("created_at", "datetime"),
        ),
        _label_from("email"),
    ),
    "activities": FileSpec(
        "activities.json",
        Activity,
        (
            Field("customer_domain", "str"),
            Field("contact_email", "str", optional=True),
            Field("article_number", "str", optional=True),
            Field("type", "str"),
            Field("occurred_at", "datetime"),
            Field("subject", "str"),
            Field("summary", "str"),
            Field("amount_eur", "decimal", nullable=True),
            Field("created_by", "str"),
        ),
        _label_from("customer_domain", "occurred_at"),
    ),
    "discount_rules": FileSpec(
        "discount_rules.json",
        DiscountRule,
        (
            Field("customer_status", "str", nullable=True),
            Field("product_category", "str", nullable=True),
            Field("min_quantity", "int"),
            Field("max_discount_percent", "decimal"),
            Field("description", "str"),
        ),
        _rule_entry_label,
    ),
}


# Umwandlung der Werte


def _describe(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "Wahrheitswert"
    if isinstance(value, int):
        return "ganze Zahl"
    if isinstance(value, float | Decimal):
        return "Dezimalzahl"
    if isinstance(value, str):
        return "Text"
    if isinstance(value, list):
        return "Liste"
    if isinstance(value, dict):
        return "Objekt"
    return type(value).__name__


def _wrong_type(expected: str, value: Any) -> ValueError:
    return ValueError(f"muss {expected} sein (gefunden: {_describe(value)})")


def _convert(kind: str, value: Any) -> Any:
    """Wandelt einen JSON-Wert in den Zieltyp um oder wirft ValueError mit der Meldung."""
    if kind == "str":
        if isinstance(value, str):
            return value
        raise _wrong_type("Text", value)
    if kind == "int":
        if isinstance(value, int) and not isinstance(value, bool):
            return value
        raise _wrong_type("eine ganze Zahl", value)
    if kind == "bool":
        if isinstance(value, bool):
            return value
        raise _wrong_type("ein Wahrheitswert (true oder false)", value)
    if kind == "decimal":
        # Ganze Zahlen und Dezimalzahlen (vom JSON-Leser als Decimal geliefert), nie float
        if isinstance(value, int) and not isinstance(value, bool):
            return Decimal(value)
        if isinstance(value, Decimal):
            return value
        raise _wrong_type("eine Zahl", value)
    if kind == "object":
        if isinstance(value, dict):
            return value
        raise _wrong_type("ein Objekt", value)
    if kind == "datetime":
        if not isinstance(value, str):
            raise _wrong_type("ein Zeitpunkt als Text", value)
        try:
            parsed = datetime.fromisoformat(value)
        except ValueError:
            raise ValueError(
                "muss ein ISO-8601-Zeitpunkt sein, z. B. 2025-01-30T09:50:00+01:00 "
                f'(gefunden: "{value}")'
            ) from None
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError(f'braucht eine Zeitzone, z. B. +01:00 (gefunden: "{value}")')
        return parsed
    raise AssertionError(f"Unbekannter Feldtyp {kind!r}")


def _convert_entry(spec: FileSpec, entry: dict[str, Any]) -> tuple[list[str], dict[str, Any]]:
    """Prüft einen Eintrag gegen die Feldbeschreibung. Gibt (Fehler, Werte) zurück."""
    errors: list[str] = []
    values: dict[str, Any] = {}
    known = {field.name for field in spec.fields}
    for key in entry:
        if key not in known:
            errors.append(f'Unbekanntes Feld "{key}"')
    for field in spec.fields:
        if field.name not in entry:
            if field.optional:
                values[field.name] = None
            else:
                errors.append(f'Pflichtfeld "{field.name}" fehlt')
            continue
        value = entry[field.name]
        if value is None:
            if field.nullable or field.optional:
                values[field.name] = None
            else:
                errors.append(f'Feld "{field.name}" darf nicht null sein')
            continue
        try:
            values[field.name] = _convert(field.kind, value)
        except ValueError as exc:
            errors.append(f'Feld "{field.name}" {exc}')
    return errors, values


# Lesen der Dateien


class _Failed:
    """Marker: Die Datei konnte nicht gelesen werden (Meldung steht schon in messages)."""


_FAILED = _Failed()


def _reject_constant(name: str) -> Any:
    raise ValueError(f"{name} ist in JSON nicht erlaubt")


def _read_json(path: Path, messages: list[str]) -> Any:
    try:
        text = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        messages.append(f"{path.name}: Datei fehlt in {path.parent}")
        return _FAILED
    except UnicodeDecodeError:
        messages.append(f"{path.name}: Datei ist nicht als UTF-8 lesbar")
        return _FAILED
    except OSError as exc:
        messages.append(f"{path.name}: Datei nicht lesbar ({exc.strerror or type(exc).__name__})")
        return _FAILED
    # Ein doppelter Schlüssel in einem Objekt würde sonst stillschweigend den letzten Wert nehmen
    duplicates: list[str] = []

    def collect_pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
        result: dict[str, Any] = {}
        for key, value in pairs:
            if key in result and key not in duplicates:
                duplicates.append(key)
            result[key] = value
        return result

    try:
        data = json.loads(
            text,
            object_pairs_hook=collect_pairs,
            parse_float=Decimal,
            parse_constant=_reject_constant,
        )
    except json.JSONDecodeError as exc:
        messages.append(
            f"{path.name}: kein gültiges JSON (Zeile {exc.lineno}, Spalte {exc.colno}): {exc.msg}"
        )
        return _FAILED
    except ValueError as exc:
        messages.append(f"{path.name}: kein gültiges JSON ({exc})")
        return _FAILED
    if duplicates:
        messages.extend(
            f'{path.name}: kein gültiges JSON (doppelter Schlüssel "{key}")' for key in duplicates
        )
        return _FAILED
    return data


def _load_file(directory: Path, spec: FileSpec, messages: list[str]) -> tuple[Any, ...]:
    data = _read_json(directory / spec.filename, messages)
    if isinstance(data, _Failed):
        return ()
    if not isinstance(data, list):
        messages.append(
            f"{spec.filename}: Die oberste Ebene muss eine Liste sein (gefunden: {_describe(data)})"
        )
        return ()
    records: list[Any] = []
    for number, entry in enumerate(data, start=1):
        if not isinstance(entry, dict):
            messages.append(
                f"{spec.filename}, Eintrag {number}: muss ein Objekt sein "
                f"(gefunden: {_describe(entry)})"
            )
            continue
        errors, values = _convert_entry(spec, entry)
        if errors:
            label = spec.label(entry)
            where = f"{spec.filename}, Eintrag {number}" + (f" ({label})" if label else "")
            messages.extend(f"{where}: {error}" for error in errors)
            continue
        records.append(spec.record(index=number, **values))
    return tuple(records)


def load_seed_data(data_dir: Path = DEFAULT_DATA_DIR) -> SeedData:
    """Liest alle sechs Dateien aus data_dir und liefert die Stammdaten.

    Wirft SeedDataError mit allen Meldungen, wenn irgendeine Datei oder ein Eintrag fehlerhaft
    ist. Es wird nicht beim ersten Fehler abgebrochen.
    """
    directory = Path(data_dir)
    messages: list[str] = []
    loaded = {name: _load_file(directory, spec, messages) for name, spec in FILES.items()}
    if messages:
        raise SeedDataError(messages)
    return SeedData(**loaded)
