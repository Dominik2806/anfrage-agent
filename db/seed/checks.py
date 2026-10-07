"""Konsistenzprüfungen der Stammdaten (F05, Quelle: docs/DATENMODELL.md Abschnitt 5).

Reine Funktionen ohne Ein- und Ausgabe: Sie bekommen die geladenen Stammdaten (loader.SeedData)
und geben eine Liste von Fehlermeldungen zurück. Eine leere Liste heißt: in Ordnung.
Die Meldungen sind deutsch und nennen Datei, laufende Nummer und Kennzeichen des Eintrags.

Prüfung 1: product_fits verbindet ein Ersatzteil mit einer Anlage oder einem Gehäuse.
Prüfung 2: Die Domain der Kontakt-E-Mail ist die Domain der Firma.
Prüfung 3: Aktivitäten liegen nicht vor der Anlage der Firma. Prüfung 3b: Kontakte auch nicht.
Prüfung 4: Verweise lösen auf, natürliche Schlüssel sind eindeutig, Kontakt und Aktivität
           gehören zur selben Firma.
Prüfung 5: Artikelnummern in Freitexten existieren im Katalog.

Ist ein Verweis nicht auflösbar, meldet nur Prüfung 4 ihn. Die übrigen Prüfungen überspringen
den Eintrag, damit aus einem Fehler keine Folgemeldungen entstehen.
"""

import re
from collections.abc import Callable, Hashable, Iterator, Sequence
from typing import Any

from .loader import SeedData, rule_label

# Muster einer Artikelnummer (wie CHECK products_article_number_format), nicht Teil eines Worts
ARTICLE_PATTERN = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}-[0-9]{4}(?:-[A-Z0-9]{1,4})?(?![A-Za-z0-9])")

FIT_PART_CATEGORY = "spare_part"
FIT_TARGET_CATEGORIES = ("conveyor", "housing")


def _where(filename: str, index: int, label: str) -> str:
    return f"{filename}, Eintrag {index} ({label})"


def _fit_label(fit: Any) -> str:
    return f"{fit.part} / {fit.fits}"


def _activity_label(activity: Any) -> str:
    return f"{activity.customer_domain}, {activity.occurred_at.isoformat()}"


def _rule_label(rule: Any) -> str:
    return rule_label(rule.customer_status, rule.product_category, rule.min_quantity)


def check_fit_categories(data: SeedData) -> list[str]:
    """Prüfung 1: part ist ein Ersatzteil, fits ist ein Förderer oder ein Gehäuse."""
    category = {product.article_number: product.category for product in data.products}
    errors: list[str] = []
    for fit in data.product_fits:
        where = _where("product_fits.json", fit.index, _fit_label(fit))
        part_category = category.get(fit.part)
        fits_category = category.get(fit.fits)
        if part_category is not None and part_category != FIT_PART_CATEGORY:
            errors.append(
                f'{where}: "part" ist kein Ersatzteil (Kategorie "{part_category}", '
                f'erwartet "{FIT_PART_CATEGORY}") (Prüfung 1).'
            )
        if fits_category is not None and fits_category not in FIT_TARGET_CATEGORIES:
            errors.append(
                f'{where}: "fits" ist weder Förderer noch Gehäuse (Kategorie "{fits_category}", '
                f'erwartet "conveyor" oder "housing") (Prüfung 1).'
            )
    return errors


def check_contact_domains(data: SeedData) -> list[str]:
    """Prüfung 2: Die Domain der E-Mail entspricht customer_domain."""
    domains = {customer.domain for customer in data.customers}
    errors: list[str] = []
    for contact in data.contacts:
        if contact.customer_domain not in domains:
            continue
        where = _where("contacts.json", contact.index, contact.email)
        if "@" not in contact.email:
            errors.append(f"{where}: Die E-Mail-Adresse enthält kein @ (Prüfung 2).")
            continue
        email_domain = contact.email.rpartition("@")[2].lower()
        if email_domain != contact.customer_domain.lower():
            errors.append(
                f'{where}: E-Mail-Domain "{email_domain}" passt nicht zu customer_domain '
                f'"{contact.customer_domain}" (Prüfung 2).'
            )
    return errors


def check_activity_dates(data: SeedData) -> list[str]:
    """Prüfung 3: occurred_at liegt nicht vor created_at der Firma (zeitzonenbewusst)."""
    created = {customer.domain: customer.created_at for customer in data.customers}
    errors: list[str] = []
    for activity in data.activities:
        company_created = created.get(activity.customer_domain)
        if company_created is not None and activity.occurred_at < company_created:
            where = _where("activities.json", activity.index, _activity_label(activity))
            errors.append(
                f"{where}: occurred_at {activity.occurred_at.isoformat()} liegt vor created_at "
                f"{company_created.isoformat()} der Firma {activity.customer_domain} (Prüfung 3)."
            )
    return errors


def check_contact_dates(data: SeedData) -> list[str]:
    """Prüfung 3b: created_at eines Kontakts liegt nicht vor created_at der Firma."""
    created = {customer.domain: customer.created_at for customer in data.customers}
    errors: list[str] = []
    for contact in data.contacts:
        company_created = created.get(contact.customer_domain)
        if company_created is not None and contact.created_at < company_created:
            where = _where("contacts.json", contact.index, contact.email)
            errors.append(
                f"{where}: created_at {contact.created_at.isoformat()} liegt vor created_at "
                f"{company_created.isoformat()} der Firma {contact.customer_domain} (Prüfung 3b)."
            )
    return errors


def _duplicates(
    filename: str,
    records: Sequence[Any],
    key: Callable[[Any], Hashable],
    field: str,
    label: Callable[[Any], str],
) -> list[str]:
    first: dict[Hashable, int] = {}
    errors: list[str] = []
    for record in records:
        value = key(record)
        if value in first:
            where = _where(filename, record.index, label(record))
            errors.append(
                f'{where}: Schlüssel "{field}" doppelt, schon in Eintrag {first[value]} (Prüfung 4).'
            )
        else:
            first[value] = record.index
    return errors


def check_references_and_keys(data: SeedData) -> list[str]:
    """Prüfung 4: Verweise lösen auf, natürliche Schlüssel sind eindeutig."""
    errors: list[str] = []
    errors += _duplicates(
        "products.json",
        data.products,
        lambda p: p.article_number,
        "article_number",
        lambda p: p.article_number,
    )
    errors += _duplicates(
        "customers.json", data.customers, lambda c: c.domain, "domain", lambda c: c.domain
    )
    errors += _duplicates(
        "customers.json",
        data.customers,
        lambda c: c.company_name.casefold(),
        "company_name (ohne Beachtung der Groß-/Kleinschreibung)",
        lambda c: c.company_name,
    )
    errors += _duplicates(
        "contacts.json", data.contacts, lambda c: c.email.lower(), "email", lambda c: c.email
    )
    errors += _duplicates(
        "discount_rules.json",
        data.discount_rules,
        lambda r: (r.customer_status, r.product_category, r.min_quantity),
        "Geltungsbereich (customer_status, product_category, min_quantity)",
        _rule_label,
    )
    errors += _duplicates(
        "product_fits.json",
        data.product_fits,
        lambda f: (f.part, f.fits),
        "part und fits",
        _fit_label,
    )

    articles = {product.article_number for product in data.products}
    domains = {customer.domain for customer in data.customers}
    contact_company: dict[str, str] = {}
    for contact in data.contacts:
        contact_company.setdefault(contact.email, contact.customer_domain)

    for contact in data.contacts:
        if contact.customer_domain not in domains:
            where = _where("contacts.json", contact.index, contact.email)
            errors.append(
                f'{where}: customer_domain "{contact.customer_domain}" steht nicht in '
                "customers.json (Prüfung 4)."
            )

    for fit in data.product_fits:
        where = _where("product_fits.json", fit.index, _fit_label(fit))
        for field, value in (("part", fit.part), ("fits", fit.fits)):
            if value not in articles:
                errors.append(
                    f'{where}: {field} "{value}" steht nicht in products.json (Prüfung 4).'
                )
        if fit.part == fit.fits:
            errors.append(f"{where}: part und fits dürfen nicht derselbe Artikel sein (Prüfung 4).")

    for activity in data.activities:
        where = _where("activities.json", activity.index, _activity_label(activity))
        if activity.customer_domain not in domains:
            errors.append(
                f'{where}: customer_domain "{activity.customer_domain}" steht nicht in '
                "customers.json (Prüfung 4)."
            )
        if activity.contact_email is not None:
            company = contact_company.get(activity.contact_email)
            if company is None:
                errors.append(
                    f'{where}: contact_email "{activity.contact_email}" steht nicht in '
                    "contacts.json (Prüfung 4)."
                )
            elif company != activity.customer_domain:
                errors.append(
                    f'{where}: contact_email "{activity.contact_email}" gehört zur Firma '
                    f'"{company}", die Aktivität aber zu "{activity.customer_domain}" '
                    "(Prüfung 4)."
                )
        if activity.article_number is not None and activity.article_number not in articles:
            errors.append(
                f'{where}: article_number "{activity.article_number}" steht nicht in '
                "products.json (Prüfung 4)."
            )
    return errors


def _strings(value: Any) -> Iterator[str]:
    """Alle Texte in einem JSON-Wert (Werte von Objekten und Listen, nicht die Schlüssel)."""
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def check_article_numbers_in_text(data: SeedData) -> list[str]:
    """Prüfung 5: Artikelnummern in Freitexten existieren im Katalog."""
    known = {product.article_number for product in data.products}
    errors: list[str] = []

    def scan(where: str, field: str, text: str) -> None:
        for match in ARTICLE_PATTERN.finditer(text):
            if match.group() not in known:
                errors.append(
                    f'{where}: Feld "{field}" nennt die Artikelnummer "{match.group()}", die nicht '
                    "im Katalog steht (Prüfung 5)."
                )

    for product in data.products:
        where = _where("products.json", product.index, product.article_number)
        scan(where, "name", product.name)
        scan(where, "description", product.description)
        for text in _strings(product.technical_data):
            scan(where, "technical_data", text)
    for fit in data.product_fits:
        if fit.note is not None:
            scan(_where("product_fits.json", fit.index, _fit_label(fit)), "note", fit.note)
    for activity in data.activities:
        where = _where("activities.json", activity.index, _activity_label(activity))
        scan(where, "subject", activity.subject)
        scan(where, "summary", activity.summary)
    for rule in data.discount_rules:
        scan(
            _where("discount_rules.json", rule.index, _rule_label(rule)),
            "description",
            rule.description,
        )
    return errors


def run_all_checks(data: SeedData) -> list[str]:
    """Führt alle Prüfungen aus (Reihenfolge 1, 2, 3, 3b, 4, 5) und fasst die Meldungen zusammen."""
    errors: list[str] = []
    for check in (
        check_fit_categories,
        check_contact_domains,
        check_activity_dates,
        check_contact_dates,
        check_references_and_keys,
        check_article_numbers_in_text,
    ):
        errors += check(data)
    return errors
