"""Datentests der Stammdaten (F05, ohne Datenbank).

Sie halten fest, was data/stammdaten/README.md über die Aktivitäten und Texte behauptet:
Beträge sind Listenpreis mal ganzzahlige Menge, die Aktivitäten sind nach Zeit sortiert, alle
Domains und E-Mail-Adressen enden auf .example, und in den Texten stehen keine Telefonnummern.
Zu jeder Prüfung gibt es einen Negativtest auf einer veränderten Kopie der Daten.
"""

import re

import pytest

# Ganze Menge: "Menge 12 Meter", "Menge 1." oder "quantity 2 pieces", nicht "Menge 1,5 Stück"
QUANTITY = re.compile(r"(?:Menge|quantity) (\d+)(?![\d,.]\d)")

# Muster für Telefonnummern. Beträge (19800.0, 1540.00), Datumsangaben (2025-01-30, 30.01.2025,
# 2025-01-30T09:50:00+01:00) und Artikelnummern (FB-1001-B8) dürfen nicht auslösen.
PHONE_PATTERNS = (
    # international: +49 212 123456, +41 44 123 45 67, +49-212-123456
    re.compile(r"\+\d{1,3}[\s\-./]?\(?\d{1,4}\)?[\s\-./]?\d[\d\s\-./]{4,}"),
    # national mit führender Null und Trennzeichen: 0212/123456, 0212 123456, 0212-123456
    re.compile(r"(?<![\d.,:/\-])0\d{2,5}[\s/\-]\d{3,}"),
    # Vorwahl in Klammern: (0212) 123-456
    re.compile(r"\(\d{2,5}\)\s?\d{2,}"),
    # lange Ziffernfolge ohne Trennzeichen: 02131234567
    re.compile(r"(?<![\d.,])\d{8,}(?![\d])"),
)


def _phone_like(text):
    return [pattern.pattern for pattern in PHONE_PATTERNS if pattern.search(text)]


def _strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _strings(item)
    elif isinstance(value, list):
        for item in value:
            yield from _strings(item)


def _texts(data):
    for product in data.products:
        yield f"products.json, Eintrag {product.index}", product.name
        yield f"products.json, Eintrag {product.index}", product.description
        for text in _strings(product.technical_data):
            yield f"products.json, Eintrag {product.index}", text
    for fit in data.product_fits:
        if fit.note:
            yield f"product_fits.json, Eintrag {fit.index}", fit.note
    for customer in data.customers:
        yield f"customers.json, Eintrag {customer.index}", customer.company_name
    for contact in data.contacts:
        label = f"contacts.json, Eintrag {contact.index}"
        yield label, contact.first_name
        yield label, contact.last_name
        if contact.job_title:
            yield label, contact.job_title
    for activity in data.activities:
        label = f"activities.json, Eintrag {activity.index}"
        yield label, activity.subject
        yield label, activity.summary
    for rule in data.discount_rules:
        yield f"discount_rules.json, Eintrag {rule.index}", rule.description


def _amount_errors(data):
    prices = {product.article_number: product.list_price for product in data.products}
    errors = []
    for activity in data.activities:
        if activity.amount_eur is None:
            continue
        label = f"activities.json, Eintrag {activity.index}"
        match = QUANTITY.search(activity.summary)
        if match is None:
            errors.append(f"{label}: Keine Menge in der Zusammenfassung")
        elif activity.article_number is None:
            errors.append(f"{label}: Betrag ohne Artikel")
        else:
            expected = prices[activity.article_number] * int(match.group(1))
            if activity.amount_eur != expected:
                errors.append(f"{label}: Betrag {activity.amount_eur} statt {expected}")
    return errors


def _unsorted(data):
    return [
        f"activities.json, Eintrag {current.index}"
        for previous, current in zip(data.activities, data.activities[1:], strict=False)
        if current.occurred_at < previous.occurred_at
    ]


def _not_example(data):
    values = [("customers.json", c.domain) for c in data.customers]
    for contact in data.contacts:
        values.append(("contacts.json", contact.customer_domain))
        values.append(("contacts.json", contact.email.rpartition("@")[2]))
    for activity in data.activities:
        values.append(("activities.json", activity.customer_domain))
        if activity.contact_email is not None:
            values.append(("activities.json", activity.contact_email.rpartition("@")[2]))
    return [f"{where}: {value}" for where, value in values if not value.endswith(".example")]


def _phones(data):
    return [f"{label}: {text}" for label, text in _texts(data) if _phone_like(text)]


# Echte Daten


def test_amounts_are_list_price_times_integer_quantity(real_data):
    assert _amount_errors(real_data) == []
    assert sum(1 for a in real_data.activities if a.amount_eur is not None) > 20


def test_activities_are_sorted_by_time(real_data):
    assert _unsorted(real_data) == []


def test_all_domains_and_email_addresses_end_in_example(real_data):
    assert _not_example(real_data) == []


def test_texts_contain_no_phone_numbers(real_data):
    assert _phones(real_data) == []


def test_the_text_scan_really_looks_at_many_texts(real_data):
    # Schutz davor, dass die Prüfung auf keinen Text trifft und deshalb immer leer bleibt
    assert sum(1 for _ in _texts(real_data)) > 300


# Negativtests auf veränderten Kopien


def test_wrong_amount_is_found(raw, load_raw):
    raw["activities"][0]["amount_eur"] = 9801.0
    errors = _amount_errors(load_raw(raw))
    assert len(errors) == 1
    assert errors[0].startswith("activities.json, Eintrag 1: Betrag 9801.0 statt 9800.0")


def test_amount_without_quantity_is_found(raw, load_raw):
    raw["activities"][0]["summary"] = "Bestellung eines Kurvenförderers."
    errors = _amount_errors(load_raw(raw))
    assert errors == ["activities.json, Eintrag 1: Keine Menge in der Zusammenfassung"]


def test_amount_with_fractional_quantity_is_not_accepted(raw, load_raw):
    raw["activities"][0]["summary"] = "Bestellung, Menge 1,5 Stück."
    assert _amount_errors(load_raw(raw))


def test_unsorted_activities_are_found(raw, load_raw):
    rows = raw["activities"]
    rows[3], rows[4] = rows[4], rows[3]
    assert _unsorted(load_raw(raw)) == ["activities.json, Eintrag 5"]


def test_domain_without_example_is_found(raw, load_raw):
    raw["customers"][0]["domain"] = "brenner-automotive.com"
    errors = _not_example(load_raw(raw))
    assert errors == ["customers.json: brenner-automotive.com"]


def test_email_without_example_is_found(raw, load_raw):
    raw["contacts"][0]["email"] = "katrin.albrecht@brenner-automotive.org"
    errors = _not_example(load_raw(raw))
    assert errors == ["contacts.json: brenner-automotive.org"]


def test_phone_number_in_a_text_is_found(raw, load_raw):
    raw["activities"][0]["summary"] += " Rückruf unter 0212/123456."
    errors = _phones(load_raw(raw))
    assert len(errors) == 1
    assert errors[0].startswith("activities.json, Eintrag 1")


# Das Muster erkennt echte Nummern und löst bei Beträgen, Daten und Artikelnummern nicht aus


@pytest.mark.parametrize(
    "text",
    [
        "+49 212 123456",
        "+49-212-123456",
        "+41 44 123 45 67",
        "0212/123456",
        "0212 123456",
        "0212-123456",
        "(0212) 123-456",
        "(0212) 123456",
        "Telefon 02131234567",
        "Rückruf unter +49 (0)212 123456 bitte",
    ],
)
def test_phone_pattern_detects_real_numbers(text):
    assert _phone_like(text), text


@pytest.mark.parametrize(
    "text",
    [
        "19800.0",
        "1540.00",
        "22250.0",
        "6900.0",
        "Betrag 17850.00 Euro",
        "2025-01-30",
        "30.01.2025",
        "2025-01-30T09:50:00+01:00",
        "2026-03-16T16:50:00+01:00",
        "09:50",
        "vom 5. März 2025",
        "Angebot vom 15. April 2025",
        "FB-1001-B8",
        "ET-3010",
        "WV-4003",
        "Edelstahl 1.4301",
        "IP66",
        "Gurtbreite 800 mm",
        "Verhältnis 1:20",
        "Menge 40 Stück",
        "Steigung bis 35 Grad",
        "Motor 0,75 kW",
        "Lieferzeit 0 bis 60 Tage",
        "Zeitzone +01:00",
    ],
)
def test_phone_pattern_ignores_amounts_dates_and_article_numbers(text):
    assert _phone_like(text) == [], text
