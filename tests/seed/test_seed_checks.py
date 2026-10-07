"""Tests der Konsistenzprüfungen db/seed/checks.py (F05, ohne Datenbank).

Positiv: Die echten Daten in data/stammdaten/ bestehen jede Prüfung.
Negativ: Je Prüfung wird eine Kopie der Rohdaten gezielt verändert, in einem temporären Ordner
abgelegt und mit dem Loader geladen. Die Prüfung muss genau diesen Fehler melden.
"""

import copy

import pytest

from db.seed import checks
from db.seed.loader import SeedData

ALL_CHECKS = [
    checks.check_fit_categories,
    checks.check_contact_domains,
    checks.check_activity_dates,
    checks.check_contact_dates,
    checks.check_references_and_keys,
    checks.check_article_numbers_in_text,
]


def _find(rows, field, value):
    return next(row for row in rows if row[field] == value)


def _append_copy(rows, index=0, **changes):
    """Hängt eine Kopie von rows[index] mit Änderungen an und gibt sie zurück."""
    row = copy.deepcopy(rows[index])
    row.update(changes)
    rows.append(row)
    return row


def _check_one(check, raw, load_raw):
    return check(load_raw(raw))


# Echte Daten


@pytest.mark.parametrize("check", ALL_CHECKS, ids=lambda check: check.__name__)
def test_real_data_passes_each_check(real_data, check):
    assert check(real_data) == []


def test_real_data_passes_run_all_checks(real_data):
    assert checks.run_all_checks(real_data) == []


def test_empty_data_has_no_findings():
    assert checks.run_all_checks(SeedData((), (), (), (), (), ())) == []


# Prüfung 1


def test_fit_part_must_be_a_spare_part(raw, load_raw):
    raw["product_fits"][0]["part"] = "FB-1001"
    messages = _check_one(checks.check_fit_categories, raw, load_raw)
    assert len(messages) == 1
    assert messages[0].startswith("product_fits.json, Eintrag 1 (FB-1001 / FB-1001): ")
    assert '"part" ist kein Ersatzteil (Kategorie "conveyor"' in messages[0]
    assert "(Prüfung 1)" in messages[0]


@pytest.mark.parametrize(
    ("target", "category"), [("ET-3002", "spare_part"), ("WV-4001", "service_contract")]
)
def test_fit_target_must_be_conveyor_or_housing(raw, load_raw, target, category):
    raw["product_fits"][0]["fits"] = target
    messages = _check_one(checks.check_fit_categories, raw, load_raw)
    assert len(messages) == 1
    assert f'"fits" ist weder Förderer noch Gehäuse (Kategorie "{category}"' in messages[0]


def test_housing_is_a_valid_fit_target(raw, load_raw):
    raw["product_fits"][0]["fits"] = "SG-2004"
    assert _check_one(checks.check_fit_categories, raw, load_raw) == []


# Prüfung 2


def test_contact_email_domain_must_match_company(raw, load_raw):
    contact = _find(raw["contacts"], "email", "anja.kreuzer@rheinpack.example")
    contact["email"] = "anja.kreuzer@rheinpack-gmbh.example"
    messages = _check_one(checks.check_contact_domains, raw, load_raw)
    assert len(messages) == 1
    assert 'E-Mail-Domain "rheinpack-gmbh.example"' in messages[0]
    assert 'customer_domain "rheinpack.example"' in messages[0]
    assert "contacts.json, Eintrag " in messages[0]
    assert "(Prüfung 2)" in messages[0]


def test_email_without_at_sign_is_reported(raw, load_raw):
    raw["contacts"][0]["email"] = "katrin.albrecht.brenner-automotive.example"
    messages = _check_one(checks.check_contact_domains, raw, load_raw)
    assert len(messages) == 1
    assert "kein @" in messages[0]


def test_email_domain_comparison_ignores_case(raw, load_raw):
    raw["contacts"][0]["email"] = "katrin.albrecht@BRENNER-Automotive.example"
    assert _check_one(checks.check_contact_domains, raw, load_raw) == []


def test_contact_of_unknown_company_is_left_to_check_4(raw, load_raw):
    _append_copy(
        raw["contacts"],
        0,
        email="neu.person@brenner-automotive.example",
        customer_domain="x.example",
    )
    data = load_raw(raw)
    assert checks.check_contact_domains(data) == []
    assert checks.check_contact_dates(data) == []
    assert len(checks.check_references_and_keys(data)) == 1


# Prüfung 3 und 3b


def _first_wiesental_activity(raw):
    return next(
        a for a in raw["activities"] if a["customer_domain"] == "wiesental-metallguss.example"
    )


# Wiesental wurde am 2016-09-12T10:00:00+02:00 angelegt
@pytest.mark.parametrize(
    "occurred_at",
    ["2016-09-12T09:59:59+02:00", "2016-09-12T10:00:00+05:00", "2016-09-11T23:00:00+00:00"],
)
def test_activity_before_company_creation_is_reported(raw, load_raw, occurred_at):
    _first_wiesental_activity(raw)["occurred_at"] = occurred_at
    messages = _check_one(checks.check_activity_dates, raw, load_raw)
    assert len(messages) == 1
    assert "activities.json, Eintrag 1 (wiesental-metallguss.example, " in messages[0]
    assert "liegt vor created_at 2016-09-12T10:00:00+02:00" in messages[0]
    assert "(Prüfung 3)" in messages[0]


@pytest.mark.parametrize(
    "occurred_at",
    ["2016-09-12T10:00:00+02:00", "2016-09-12T08:00:00+00:00", "2016-09-12T10:00:00+00:00"],
)
def test_activity_at_or_after_company_creation_is_fine(raw, load_raw, occurred_at):
    _first_wiesental_activity(raw)["occurred_at"] = occurred_at
    assert _check_one(checks.check_activity_dates, raw, load_raw) == []


# Brenner wurde am 2019-03-12T09:15:00+01:00 angelegt
@pytest.mark.parametrize("created_at", ["2019-03-12T09:14:59+01:00", "2019-03-12T09:15:00+02:00"])
def test_contact_before_company_creation_is_reported(raw, load_raw, created_at):
    raw["contacts"][0]["created_at"] = created_at
    messages = _check_one(checks.check_contact_dates, raw, load_raw)
    assert len(messages) == 1
    assert "contacts.json, Eintrag 1 (katrin.albrecht@brenner-automotive.example)" in messages[0]
    assert "liegt vor created_at 2019-03-12T09:15:00+01:00" in messages[0]
    assert "(Prüfung 3b)" in messages[0]


@pytest.mark.parametrize("created_at", ["2019-03-12T09:15:00+01:00", "2019-03-12T08:15:00+00:00"])
def test_contact_at_company_creation_is_fine(raw, load_raw, created_at):
    raw["contacts"][0]["created_at"] = created_at
    assert _check_one(checks.check_contact_dates, raw, load_raw) == []


# Prüfung 4


def _unknown_company_of_contact(raw):
    _append_copy(
        raw["contacts"],
        0,
        email="neu.person@brenner-automotive.example",
        customer_domain="unbekannt.example",
    )


def _unknown_company_of_activity(raw):
    # activities[2] ist die Notiz zu Wiesental ohne Kontakt
    raw["activities"][2]["customer_domain"] = "unbekannt.example"


def _unknown_contact(raw):
    raw["activities"][0]["contact_email"] = "niemand@wiesental-metallguss.example"


def _unknown_article_in_activity(raw):
    raw["activities"][0]["article_number"] = "FB-9999"


def _unknown_part(raw):
    raw["product_fits"][0]["part"] = "ET-9999"


def _unknown_target(raw):
    raw["product_fits"][0]["fits"] = "FB-9999"


def _contact_of_other_company(raw):
    raw["activities"][0]["customer_domain"] = "zeller-spritzguss.example"


def _duplicate_article_number(raw):
    _append_copy(raw["products"], 0)


def _duplicate_domain(raw):
    _append_copy(raw["customers"], 0, company_name="Andere Firma GmbH")


def _duplicate_company_name(raw):
    name = raw["customers"][0]["company_name"].upper()
    _append_copy(raw["customers"], 0, domain="andere-domain.example", company_name=name)


def _duplicate_email(raw):
    _append_copy(raw["contacts"], 0, first_name="Zweite")


def _duplicate_rule_scope(raw):
    _append_copy(raw["discount_rules"], 1)


def _duplicate_fit_pair(raw):
    _append_copy(raw["product_fits"], 0)


def _fit_to_itself(raw):
    _append_copy(raw["product_fits"], 0, fits="ET-3001")


REFERENCE_AND_KEY_CASES = [
    (
        _unknown_company_of_contact,
        ("contacts.json", 'customer_domain "unbekannt.example"', "steht nicht in customers.json"),
    ),
    (
        _unknown_company_of_activity,
        ("activities.json, Eintrag 3", 'customer_domain "unbekannt.example"'),
    ),
    (
        _unknown_contact,
        ('contact_email "niemand@wiesental-metallguss.example"', "steht nicht in contacts.json"),
    ),
    (_unknown_article_in_activity, ('article_number "FB-9999"', "steht nicht in products.json")),
    (_unknown_part, ('part "ET-9999"', "product_fits.json, Eintrag 1")),
    (_unknown_target, ('fits "FB-9999"', "product_fits.json, Eintrag 1")),
    (
        _contact_of_other_company,
        (
            "gehört zur Firma",
            '"wiesental-metallguss.example"',
            'die Aktivität aber zu "zeller-spritzguss.example"',
        ),
    ),
    (_duplicate_article_number, ('Schlüssel "article_number" doppelt', "schon in Eintrag 1")),
    (_duplicate_domain, ('Schlüssel "domain" doppelt', "customers.json")),
    (_duplicate_company_name, ('Schlüssel "company_name', "Groß-/Kleinschreibung", "BRENNER")),
    (_duplicate_email, ('Schlüssel "email" doppelt', "contacts.json")),
    (_duplicate_rule_scope, ('Schlüssel "Geltungsbereich', "discount_rules.json")),
    (_duplicate_fit_pair, ('Schlüssel "part und fits" doppelt', "product_fits.json")),
    (_fit_to_itself, ("part und fits dürfen nicht derselbe Artikel sein",)),
]


@pytest.mark.parametrize(
    ("mutate", "fragments"),
    REFERENCE_AND_KEY_CASES,
    ids=[case[0].__name__.removeprefix("_") for case in REFERENCE_AND_KEY_CASES],
)
def test_reference_and_key_violations(raw, load_raw, mutate, fragments):
    mutate(raw)
    messages = _check_one(checks.check_references_and_keys, raw, load_raw)
    assert len(messages) == 1
    for fragment in fragments:
        assert fragment in messages[0]
    assert "(Prüfung 4)" in messages[0]


# Prüfung 5


def _text_in_product_description(raw):
    raw["products"][0]["description"] += " Siehe auch FB-9999."


def _text_in_product_name(raw):
    raw["products"][0]["name"] = "Gurtförderer wie FB-9999"


def _text_in_nested_technical_data(raw):
    raw["products"][0]["technical_data"]["parts"] = [{"ref": "FB-9999"}]


def _text_in_fit_note(raw):
    raw["product_fits"][0]["note"] = "Passt auch zu FB-9999"


def _text_in_activity_subject(raw):
    raw["activities"][0]["subject"] = "Auftrag FB-9999"


def _text_in_activity_summary(raw):
    raw["activities"][0]["summary"] += " Ersatz für FB-9999."


def _text_in_rule_description(raw):
    raw["discount_rules"][0]["description"] += " wie FB-9999"


ARTICLE_TEXT_CASES = [
    (_text_in_product_description, "description"),
    (_text_in_product_name, "name"),
    (_text_in_nested_technical_data, "technical_data"),
    (_text_in_fit_note, "note"),
    (_text_in_activity_subject, "subject"),
    (_text_in_activity_summary, "summary"),
    (_text_in_rule_description, "description"),
]


@pytest.mark.parametrize(
    ("mutate", "field"),
    ARTICLE_TEXT_CASES,
    ids=[case[0].__name__.removeprefix("_text_in_") for case in ARTICLE_TEXT_CASES],
)
def test_unknown_article_number_in_free_text_is_reported(raw, load_raw, mutate, field):
    mutate(raw)
    messages = _check_one(checks.check_article_numbers_in_text, raw, load_raw)
    assert len(messages) == 1
    assert f'Feld "{field}" nennt die Artikelnummer "FB-9999"' in messages[0]
    assert "(Prüfung 5)" in messages[0]


def test_existing_article_numbers_and_variants_in_text_are_fine(raw, load_raw):
    raw["activities"][0]["summary"] += " Variante FB-1001-B8 und ET-3010 und WV-4001."
    assert _check_one(checks.check_article_numbers_in_text, raw, load_raw) == []


def test_unknown_variant_of_existing_article_is_reported(raw, load_raw):
    raw["activities"][0]["summary"] += " Siehe FB-1001-Z9."
    messages = _check_one(checks.check_article_numbers_in_text, raw, load_raw)
    assert len(messages) == 1
    assert 'Artikelnummer "FB-1001-Z9"' in messages[0]


def test_each_unknown_number_in_a_text_is_reported(raw, load_raw):
    raw["activities"][0]["summary"] += " FB-9998 und FB-9999."
    messages = _check_one(checks.check_article_numbers_in_text, raw, load_raw)
    assert len(messages) == 2


def test_numbers_inside_words_and_lowercase_are_not_matches(raw, load_raw):
    raw["activities"][0]["summary"] += " XFB-9999 und FB-99991 und fb-9999 und 1FB-9999."
    assert _check_one(checks.check_article_numbers_in_text, raw, load_raw) == []


def test_only_values_of_technical_data_are_scanned(raw, load_raw):
    raw["products"][0]["technical_data"]["ZZ-9999"] = 1
    assert _check_one(checks.check_article_numbers_in_text, raw, load_raw) == []


# Mehrere Fehler


def test_run_all_checks_reports_several_findings_in_order(raw, load_raw):
    raw["product_fits"][0]["part"] = "FB-1001"  # Prüfung 1
    _append_copy(raw["contacts"], 0, email="neu.person@falsch.example")  # Prüfung 2
    raw["activities"][0]["occurred_at"] = "2016-09-11T10:00:00+02:00"  # Prüfung 3
    _append_copy(  # Prüfung 3b
        raw["contacts"],
        1,
        email="frueh.person@brenner-automotive.example",
        created_at="2019-03-01T09:00:00+01:00",
    )
    _append_copy(raw["product_fits"], 1)  # Prüfung 4 (doppeltes Paar)
    raw["products"][0]["description"] += " FB-9999"  # Prüfung 5

    messages = checks.run_all_checks(load_raw(raw))

    assert isinstance(messages, list)
    markers = [f"(Prüfung {number})" for number in ("1", "2", "3", "3b", "4", "5")]
    positions = [
        next(i for i, message in enumerate(messages) if marker in message) for marker in markers
    ]
    assert positions == sorted(set(positions))
    assert len(messages) >= 6
    # Meldungen werden auf Windows-Konsolen und in Dateien ausgegeben
    for message in messages:
        message.encode("cp1252")
