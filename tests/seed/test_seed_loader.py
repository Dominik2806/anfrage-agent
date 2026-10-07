"""Tests des Loaders db/seed/loader.py (F05, ohne Datenbank).

Positiv: Die echten Daten in data/stammdaten/ laden fehlerfrei und sind unveränderlich.
Negativ: Jede Formverletzung (fehlendes Pflichtfeld, unbekanntes Feld, falscher Typ, kaputtes
JSON, fehlende Datei, Zeitpunkt ohne Zeitzone) wird mit Datei und Eintrag gemeldet. Mehrere
Fehler erscheinen zusammen in einer SeedDataError.
"""

from dataclasses import FrozenInstanceError
from decimal import Decimal

import pytest

from db.seed.loader import (
    DEFAULT_DATA_DIR,
    SeedData,
    SeedDataError,
    load_seed_data,
    rule_label,
)


def _messages(excinfo) -> tuple[str, ...]:
    return excinfo.value.messages


def test_real_data_loads(real_data):
    assert isinstance(real_data, SeedData)
    # Bereiche statt exakter Zahlen, damit spätere Features Daten ergänzen können
    assert 35 <= len(real_data.products) <= 45
    assert len(real_data.product_fits) >= 20
    assert 18 <= len(real_data.customers) <= 25
    assert 30 <= len(real_data.contacts) <= 45
    assert 60 <= len(real_data.activities) <= 100
    assert 15 <= len(real_data.discount_rules) <= 25
    for rows in (
        real_data.products,
        real_data.product_fits,
        real_data.customers,
        real_data.contacts,
        real_data.activities,
        real_data.discount_rules,
    ):
        assert isinstance(rows, tuple)


def test_default_data_dir_points_to_real_data(data_dir):
    assert DEFAULT_DATA_DIR == data_dir
    assert load_seed_data() == load_seed_data(data_dir)


def test_umlauts_are_read_as_utf8(real_data):
    assert any("Präzisionsteile" in customer.company_name for customer in real_data.customers)


def test_records_are_numbered_from_one(real_data):
    assert [p.index for p in real_data.products] == list(range(1, len(real_data.products) + 1))


def test_data_is_immutable(real_data):
    with pytest.raises(FrozenInstanceError):
        real_data.products = ()
    with pytest.raises(FrozenInstanceError):
        real_data.products[0].name = "anders"


def test_numbers_are_decimal_without_float_noise(real_data):
    product = next(p for p in real_data.products if p.article_number == "ET-3006")
    assert product.list_price == Decimal("38.5")
    assert isinstance(product.list_price, Decimal)
    amounts = [a.amount_eur for a in real_data.activities if a.amount_eur is not None]
    assert amounts and all(isinstance(amount, Decimal) for amount in amounts)
    assert all(isinstance(r.max_discount_percent, Decimal) for r in real_data.discount_rules)


def test_nullable_values_are_none(real_data):
    inquiry = next(a for a in real_data.activities if a.type == "inquiry")
    assert inquiry.amount_eur is None
    rule = next(r for r in real_data.discount_rules if r.customer_status is None)
    assert rule.product_category is None


def test_timestamps_are_timezone_aware(real_data):
    assert all(c.created_at.utcoffset() is not None for c in real_data.customers)
    assert all(a.occurred_at.utcoffset() is not None for a in real_data.activities)


def test_integers_and_decimals_are_both_accepted_and_equal(raw, load_raw):
    raw["products"][0]["list_price"] = 940
    raw["products"][1]["list_price"] = 940.0
    raw["discount_rules"][1]["max_discount_percent"] = 3
    data = load_raw(raw)
    assert data.products[0].list_price == data.products[1].list_price == Decimal("940")
    assert isinstance(data.products[0].list_price, Decimal)
    assert isinstance(data.products[1].list_price, Decimal)
    assert data.discount_rules[1].max_discount_percent == Decimal("3")


def test_optional_keys_may_be_missing(raw, load_raw):
    del raw["activities"][0]["contact_email"]
    del raw["activities"][0]["article_number"]
    del raw["product_fits"][0]["note"]
    del raw["contacts"][0]["job_title"]
    data = load_raw(raw)
    assert data.activities[0].contact_email is None
    assert data.activities[0].article_number is None
    assert data.product_fits[0].note is None
    assert data.contacts[0].job_title is None


def test_empty_list_is_valid(raw, load_raw):
    raw["activities"] = []
    assert load_raw(raw).activities == ()


def test_missing_required_field(raw, load_raw):
    del raw["products"][0]["name"]
    with pytest.raises(SeedDataError) as excinfo:
        load_raw(raw)
    messages = _messages(excinfo)
    assert len(messages) == 1
    assert "products.json, Eintrag 1 (FB-1001)" in messages[0]
    assert 'Pflichtfeld "name" fehlt' in messages[0]


@pytest.mark.parametrize(
    ("file", "field"),
    [
        ("activities", "amount_eur"),
        ("discount_rules", "customer_status"),
        ("discount_rules", "product_category"),
    ],
)
def test_nullable_fields_must_still_be_present(raw, load_raw, file, field):
    del raw[file][0][field]
    with pytest.raises(SeedDataError) as excinfo:
        load_raw(raw)
    assert any(f'Pflichtfeld "{field}" fehlt' in message for message in _messages(excinfo))


def test_unknown_field_is_reported(raw, load_raw):
    raw["customers"][2]["staus"] = "existing"
    with pytest.raises(SeedDataError) as excinfo:
        load_raw(raw)
    messages = _messages(excinfo)
    assert len(messages) == 1
    assert "customers.json, Eintrag 3 (rheinpack.example)" in messages[0]
    assert 'Unbekanntes Feld "staus"' in messages[0]


def test_misspelled_field_gives_unknown_and_missing(raw, load_raw):
    raw["contacts"][0]["emial"] = raw["contacts"][0].pop("email")
    with pytest.raises(SeedDataError) as excinfo:
        load_raw(raw)
    text = str(excinfo.value)
    assert 'Unbekanntes Feld "emial"' in text
    assert 'Pflichtfeld "email" fehlt' in text


@pytest.mark.parametrize(
    ("file", "field", "value", "fragment"),
    [
        ("products", "list_price", "890", "muss eine Zahl sein (gefunden: Text)"),
        ("products", "list_price", True, "muss eine Zahl sein (gefunden: Wahrheitswert)"),
        ("products", "is_active", "ja", "muss ein Wahrheitswert"),
        ("products", "lead_time_days", True, "muss eine ganze Zahl sein"),
        ("products", "lead_time_days", 5.5, "muss eine ganze Zahl sein (gefunden: Dezimalzahl)"),
        ("products", "technical_data", [], "muss ein Objekt sein (gefunden: Liste)"),
        ("products", "name", 5, "muss Text sein (gefunden: ganze Zahl)"),
        ("contacts", "first_name", ["Erika"], "muss Text sein (gefunden: Liste)"),
        ("discount_rules", "min_quantity", True, "muss eine ganze Zahl sein"),
        ("discount_rules", "min_quantity", 10.0, "muss eine ganze Zahl sein"),
        ("discount_rules", "max_discount_percent", "3", "muss eine Zahl sein"),
        ("activities", "amount_eur", "940", "muss eine Zahl sein"),
    ],
)
def test_wrong_type_is_reported(raw, load_raw, file, field, value, fragment):
    raw[file][0][field] = value
    with pytest.raises(SeedDataError) as excinfo:
        load_raw(raw)
    messages = _messages(excinfo)
    assert len(messages) == 1
    assert f"{file}.json, Eintrag 1" in messages[0]
    assert f'Feld "{field}"' in messages[0]
    assert fragment in messages[0]


@pytest.mark.parametrize(
    ("file", "field", "value", "fragment"),
    [
        ("customers", "created_at", "2019-03-12T09:15:00", "braucht eine Zeitzone"),
        ("customers", "created_at", "2019-03-12", "braucht eine Zeitzone"),
        ("customers", "created_at", "gestern", "muss ein ISO-8601-Zeitpunkt sein"),
        ("activities", "occurred_at", 5, "muss ein Zeitpunkt als Text sein"),
        ("contacts", "created_at", "2019-13-45T00:00:00+01:00", "muss ein ISO-8601-Zeitpunkt"),
    ],
)
def test_invalid_timestamp_is_reported(raw, load_raw, file, field, value, fragment):
    raw[file][0][field] = value
    with pytest.raises(SeedDataError) as excinfo:
        load_raw(raw)
    messages = _messages(excinfo)
    assert len(messages) == 1
    assert fragment in messages[0]


def test_null_in_required_field_is_reported(raw, load_raw):
    raw["customers"][0]["status"] = None
    with pytest.raises(SeedDataError) as excinfo:
        load_raw(raw)
    assert 'Feld "status" darf nicht null sein' in str(excinfo.value)


def test_entry_that_is_not_an_object(raw, load_raw):
    raw["products"][0] = "FB-1001"
    with pytest.raises(SeedDataError) as excinfo:
        load_raw(raw)
    messages = _messages(excinfo)
    assert len(messages) == 1
    assert "products.json, Eintrag 1: muss ein Objekt sein (gefunden: Text)" in messages[0]


def test_several_errors_in_one_entry_are_all_reported(raw, load_raw):
    entry = raw["products"][3]
    del entry["name"]
    entry["list_price"] = "teuer"
    entry["farbe"] = "rot"
    with pytest.raises(SeedDataError) as excinfo:
        load_raw(raw)
    messages = _messages(excinfo)
    assert len(messages) == 3
    assert all("products.json, Eintrag 4 (FB-1003)" in message for message in messages)


def test_invalid_json(raw, write_raw):
    directory = write_raw(raw)
    (directory / "customers.json").write_text("[{kaputt", encoding="utf-8")
    with pytest.raises(SeedDataError) as excinfo:
        load_seed_data(directory)
    messages = _messages(excinfo)
    assert len(messages) == 1
    assert "customers.json: kein gültiges JSON" in messages[0]


def test_nan_is_not_valid_json(raw, write_raw):
    directory = write_raw(raw)
    (directory / "products.json").write_text("[NaN]", encoding="utf-8")
    with pytest.raises(SeedDataError) as excinfo:
        load_seed_data(directory)
    assert "products.json: kein gültiges JSON" in str(excinfo.value)


def test_duplicate_key_in_json_object_is_reported(raw, write_raw):
    directory = write_raw(raw)
    text = '[{"article_number": "FB-1001", "name": "Erster", "name": "Zweiter"}]'
    (directory / "products.json").write_text(text, encoding="utf-8")
    with pytest.raises(SeedDataError) as excinfo:
        load_seed_data(directory)
    assert _messages(excinfo) == ('products.json: kein gültiges JSON (doppelter Schlüssel "name")',)


def test_duplicate_key_in_nested_object_is_reported(raw, write_raw):
    directory = write_raw(raw)
    text = '[{"article_number": "FB-1001", "technical_data": {"belt_width_mm": 500, "belt_width_mm": 800}}]'
    (directory / "products.json").write_text(text, encoding="utf-8")
    with pytest.raises(SeedDataError) as excinfo:
        load_seed_data(directory)
    assert 'doppelter Schlüssel "belt_width_mm"' in str(excinfo.value)


def test_each_duplicate_key_is_reported_once(raw, write_raw):
    directory = write_raw(raw)
    text = '[{"a": 1, "a": 2, "a": 3}, {"b": 1, "b": 2}, {"a": 4, "a": 5}]'
    (directory / "contacts.json").write_text(text, encoding="utf-8")
    with pytest.raises(SeedDataError) as excinfo:
        load_seed_data(directory)
    assert _messages(excinfo) == (
        'contacts.json: kein gültiges JSON (doppelter Schlüssel "a")',
        'contacts.json: kein gültiges JSON (doppelter Schlüssel "b")',
    )


def test_rule_label_is_used_in_loader_messages(raw, load_raw):
    assert rule_label(None, None, 1) == "alle, alle, ab 1"
    assert rule_label("existing", "conveyor", 10) == "existing, conveyor, ab 10"
    raw["discount_rules"][1]["min_quantity"] = "zehn"
    with pytest.raises(SeedDataError) as excinfo:
        load_raw(raw)
    assert "discount_rules.json, Eintrag 2 (existing, conveyor, ab zehn)" in str(excinfo.value)


def test_messages_can_be_encoded_in_cp1252(raw, write_raw):
    del raw["products"][0]["name"]
    raw["product_fits"][0]["part"] = 5
    directory = write_raw(raw)
    (directory / "customers.json").write_text('[{"domain": "a", "domain": "b"}]', encoding="utf-8")
    with pytest.raises(SeedDataError) as excinfo:
        load_seed_data(directory)
    assert len(_messages(excinfo)) >= 3
    for message in _messages(excinfo):
        message.encode("cp1252")


def test_top_level_must_be_a_list(raw, write_raw):
    directory = write_raw(raw)
    (directory / "contacts.json").write_text('{"contacts": []}', encoding="utf-8")
    with pytest.raises(SeedDataError) as excinfo:
        load_seed_data(directory)
    messages = _messages(excinfo)
    assert len(messages) == 1
    assert "contacts.json: Die oberste Ebene muss eine Liste sein (gefunden: Objekt)" in messages[0]


def test_missing_file(raw, write_raw):
    directory = write_raw(raw)
    (directory / "discount_rules.json").unlink()
    with pytest.raises(SeedDataError) as excinfo:
        load_seed_data(directory)
    messages = _messages(excinfo)
    assert len(messages) == 1
    assert "discount_rules.json: Datei fehlt" in messages[0]


def test_file_that_is_not_utf8(raw, write_raw):
    directory = write_raw(raw)
    (directory / "products.json").write_bytes(b"[\xff\xfe]")
    with pytest.raises(SeedDataError) as excinfo:
        load_seed_data(directory)
    assert "products.json: Datei ist nicht als UTF-8 lesbar" in str(excinfo.value)


def test_missing_directory_reports_every_file(tmp_path):
    with pytest.raises(SeedDataError) as excinfo:
        load_seed_data(tmp_path / "gibt-es-nicht")
    messages = _messages(excinfo)
    assert len(messages) == 6
    assert all("Datei fehlt" in message for message in messages)


def test_errors_from_several_files_are_reported_together(raw, write_raw):
    del raw["products"][0]["name"]
    raw["customers"][0]["staus"] = "existing"
    directory = write_raw(raw)
    (directory / "contacts.json").unlink()
    (directory / "activities.json").write_text("nicht json", encoding="utf-8")
    with pytest.raises(SeedDataError) as excinfo:
        load_seed_data(directory)
    messages = _messages(excinfo)
    assert len(messages) == 4
    for filename in ("products.json", "customers.json", "contacts.json", "activities.json"):
        assert sum(filename in message for message in messages) == 1
    # str() gibt alle Meldungen zeilenweise aus
    assert str(excinfo.value).splitlines() == list(messages)
