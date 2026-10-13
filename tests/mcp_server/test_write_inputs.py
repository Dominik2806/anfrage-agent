"""Tests der Eingabeprüfung der Schreib-Werkzeuge create_lead und log_activity: hoffmann_data/limits.py (F08, Etappe 4a).

Jede Prüfung gibt den bereinigten Wert zurück oder wirft einen ToolError mit fester Meldung. Die Meldung enthält nie
den Eingabewert (Kundentext ist Daten, kein Echo), und die Prüfungen loggen nichts. Länge zählt in Zeichen (Python
len), nicht in Bytes; Leerraum an den Rändern wird entfernt, bevor gezählt wird. Ohne Datenbank und ohne Netzwerk.

Festgelegte Regeln (Antworten zu Domain, E-Mail, Aufzählungen und Wagenrücklauf stehen im Plan zu Etappe 4a):
- Firmenname 1 bis 200, Vorname und Nachname je 1 bis 100 Zeichen, Position höchstens 100 (fehlt sie, ist sie leer oder
  nur Leerraum: None). Diese Felder sind einzeilig: Steuer-, Format- und Trennzeichen (Unicode-Kategorien C*, Zl, Zp,
  auch Zeilenumbruch und Tab) werden abgelehnt.
- E-Mail höchstens 254 Zeichen, genau ein @, Lokalteil und Domain nicht leer, kein Leerraum, keine Steuer-, Format- und
  Trennzeichen. Die Domain folgt dem Schema (nur [a-z0-9-]-Labels, endet auf .example). Zurückgegeben wird die ganze
  Adresse in Kleinbuchstaben (das Schema verlangt email = lower(email)). email_domain liefert die Domain.
- check_domain (Kundendomain von log_activity und Domain der Adresse): dieselbe Regel wie im Schema, klein
  zurückgegeben, höchstens 253 Zeichen. Der Abgleich der Suche in crm.py (find_customer) ist eine andere, lockere Regel.
- Sprache (de, en), Land (de, at, ch; Pflicht), Branche (sieben Werte; fehlt sie: other) und Aktivitätstyp (inquiry,
  complaint, note) nur exakt so, wie sie im Schema stehen: kein strip, kein lower. Das Schema ist die Quelle.
- Betreff 1 bis 200, Zusammenfassung 1 bis 2000 Zeichen. Erlaubt sind Zeilenumbruch, Tab und Wagenrücklauf; alle anderen
  Zeichen der Kategorien C* sowie Zl und Zp werden abgelehnt (NUL kann PostgreSQL nicht speichern).

Sonderzeichen entstehen mit chr(), nicht als Escape im Quelltext (ein \\uXXXX würde vom Werkzeug wörtlich eingesetzt).
Rot, bis hoffmann_data/limits.py die Konstanten und Funktionen kennt (TDD).
"""

import logging
import re
from typing import Any

import pytest
from mcp.server.mcpserver.exceptions import ToolError
from mcp_testkit import ROOT, SECRET_MARKER

SCHEMA_SQL = ROOT / "db" / "schema.sql"
WRITE_ROLE_SQL = ROOT / "db" / "roles" / "data_service_rw.sql"

# Sonderzeichen mit chr(), je Unicode-Kategorie ein Beispiel
NUL = chr(0x00)
CONTROL_CHARACTERS = {
    "nul": chr(0x00),
    "soh": chr(0x01),
    "esc": chr(0x1B),
    "del": chr(0x7F),
    "nel": chr(0x85),
}
OTHER_REJECTED_CHARACTERS = {
    "nullbreite-cf": chr(0x200B),
    "bidi-ueberschreibung-cf": chr(0x202E),
    "zeilentrenner-zl": chr(0x2028),
    "absatztrenner-zp": chr(0x2029),
    "privater-bereich-co": chr(0xE000),
    "nicht-zugewiesen-cn": chr(0x0378),
    "einzelnes-surrogat-cs": chr(0xD800),
}
# Zeichen, die einzeilige Felder zusätzlich ablehnen (Betreff und Zusammenfassung erlauben sie)
LAYOUT_CHARACTERS = {"zeilenumbruch": "\n", "tabulator": "\t", "wagenruecklauf": "\r"}
SINGLE_LINE_REJECTED = {**CONTROL_CHARACTERS, **OTHER_REJECTED_CHARACTERS, **LAYOUT_CHARACTERS}
MULTI_LINE_REJECTED = {**CONTROL_CHARACTERS, **OTHER_REJECTED_CHARACTERS}

EMOJI = chr(0x1F600)  # ein Zeichen, vier Bytes in UTF-8
COMBINING_ACUTE = chr(0x0301)
NO_BREAK_SPACE = chr(0x00A0)
EM_SPACE = chr(0x2003)
NON_STRINGS = [123, 1.5, True, False, ["a"], {"a": 1}, b"abc", ("a",)]

# Feld -> (Funktion, Meldung, Wörter, die die Meldung nennt: Feldname und Regel)
MESSAGES = {
    "COMPANY_NAME_INVALID": ("Firmenname", "200"),
    "FIRST_NAME_INVALID": ("Vorname", "100"),
    "LAST_NAME_INVALID": ("Nachname", "100"),
    "JOB_TITLE_INVALID": ("Position", "100"),
    "EMAIL_INVALID": ("E-Mail", "254"),
    "DOMAIN_INVALID": ("Domain", ".example"),
    "LANGUAGE_INVALID": ("Sprache", "de"),
    "COUNTRY_INVALID": ("Land", "ch"),
    "INDUSTRY_INVALID": ("Branche", "other"),
    "ACTIVITY_TYPE_INVALID": ("Aktivitätstyp", "complaint"),
    "SUBJECT_INVALID": ("Betreff", "200"),
    "SUMMARY_INVALID": ("Zusammenfassung", "2000"),
}
# Jede Prüfung mit ihrer Meldung. optional_none: None ist ein gültiger Wert (Position, Branche).
CHECKS = [
    pytest.param("check_company_name", "COMPANY_NAME_INVALID", False, id="firma"),
    pytest.param("check_first_name", "FIRST_NAME_INVALID", False, id="vorname"),
    pytest.param("check_last_name", "LAST_NAME_INVALID", False, id="nachname"),
    pytest.param("check_job_title", "JOB_TITLE_INVALID", True, id="position"),
    pytest.param("check_email", "EMAIL_INVALID", False, id="email"),
    pytest.param("check_domain", "DOMAIN_INVALID", False, id="domain"),
    pytest.param("check_language", "LANGUAGE_INVALID", False, id="sprache"),
    pytest.param("check_country", "COUNTRY_INVALID", False, id="land"),
    pytest.param("check_industry", "INDUSTRY_INVALID", True, id="branche"),
    pytest.param("check_activity_type", "ACTIVITY_TYPE_INVALID", False, id="aktivitaetstyp"),
    pytest.param("check_subject", "SUBJECT_INVALID", False, id="betreff"),
    pytest.param("check_summary", "SUMMARY_INVALID", False, id="zusammenfassung"),
]


def _limits() -> Any:
    from hoffmann_data import limits

    return limits


def _call(function: str, value: Any) -> Any:
    return getattr(_limits(), function)(value)


def _message(name: str) -> str:
    return str(getattr(_limits(), name))


def assert_rejected(function: str, message: str, value: Any) -> None:
    """Die Prüfung wirft ToolError mit der festen Meldung; die Meldung enthält den Wert nie."""
    with pytest.raises(ToolError) as info:
        _call(function, value)
    text = str(info.value)
    assert text == _message(message)
    assert SECRET_MARKER not in text
    # Kurze Werte (z. B. "food") dürfen in einer Meldung stehen, die die erlaubten Werte nennt; Echo prüfen nur lange
    if isinstance(value, str) and len(value.strip()) > 30:
        assert value.strip() not in text


# Konstanten, Meldungen, Schema


def test_write_limit_constants() -> None:
    limits = _limits()
    assert limits.MAX_COMPANY_NAME_LENGTH == 200
    assert limits.MAX_PERSON_NAME_LENGTH == 100
    assert limits.MAX_JOB_TITLE_LENGTH == 100
    assert limits.MAX_EMAIL_LENGTH == 254
    assert limits.MAX_DOMAIN_LENGTH == 253
    assert limits.MAX_SUBJECT_LENGTH == 200
    assert limits.MAX_SUMMARY_LENGTH == 2000
    assert tuple(limits.ALLOWED_LANGUAGES) == ("de", "en")
    assert tuple(limits.ALLOWED_COUNTRIES) == ("de", "at", "ch")
    assert tuple(limits.ALLOWED_INDUSTRIES) == (
        "automotive",
        "food",
        "packaging",
        "metalworking",
        "plastics",
        "logistics",
        "other",
    )
    assert limits.DEFAULT_INDUSTRY == "other"
    assert tuple(limits.ACTIVITY_TYPES) == ("inquiry", "complaint", "note")


def test_messages_are_fixed_distinct_and_free_of_values() -> None:
    texts = [_message(name) for name in MESSAGES]
    assert all(t.strip() for t in texts)
    assert len(set(texts)) == len(texts) == 12
    for name, words in MESSAGES.items():
        text = _message(name)
        assert SECRET_MARKER not in text
        for word in words:
            assert word in text, f"{name} nennt {word!r} nicht"


def _schema_list(text: str, constraint: str) -> list[str]:
    found = re.search(rf"{constraint}\s+CHECK \(\w+ IN \(([^)]*)\)\)", text)
    assert found, f"CHECK {constraint} nicht in db/schema.sql gefunden"
    return re.findall(r"'([^']*)'", found.group(1))


def test_allowed_values_match_the_schema() -> None:
    limits = _limits()
    schema = SCHEMA_SQL.read_text(encoding="utf-8")
    assert list(limits.ALLOWED_LANGUAGES) == _schema_list(schema, "contacts_language_check")
    assert list(limits.ALLOWED_COUNTRIES) == _schema_list(schema, "customers_country_check")
    assert list(limits.ALLOWED_INDUSTRIES) == _schema_list(schema, "customers_industry_check")
    assert set(limits.ACTIVITY_TYPES) <= set(_schema_list(schema, "activities_type_check"))


def test_activity_types_equal_the_insert_policy_of_the_write_role() -> None:
    """Die Rolle darf nur diese Typen schreiben (WITH CHECK der INSERT-Policy auf activities)."""
    script = re.sub(r"--[^\n]*", "", WRITE_ROLE_SQL.read_text(encoding="utf-8"))
    found = re.search(r"\btype IN \(([^)]*)\)", script)
    assert found, "type IN (...) nicht im Rollen-Skript gefunden"
    assert set(re.findall(r"'([^']*)'", found.group(1))) == set(_limits().ACTIVITY_TYPES)


DOMAINS_VALID = [
    "firma.example",
    "mail.firma.example",
    "a-b.example",
    "-a.example",  # das Schema erlaubt einen Bindestrich am Anfang eines Labels
    "xn--mller-kva.example",
    "x1.y2.z3.example",
    "1.example",
    "a" * 63 + ".example",
]
DOMAINS_INVALID = [
    "example",
    ".example",
    ".firma.example",
    "firma.de",
    "firma.example.com",
    "firma.examples",
    "firma.exampl",
    "firma.example.",
    "firma..example",
    "fir_ma.example",
    "müller.example",
    "firma example",
    "http://firma.example",
    "firma.example/pfad",
    "firma.example:8080",
    "firma.example,",
    "*.example",
]


def _schema_pattern(constraint: str, anchor: str) -> re.Pattern[str]:
    schema = SCHEMA_SQL.read_text(encoding="utf-8")
    found = re.search(rf"{constraint}\s+CHECK \([^']*?{anchor} ~ '([^']+)'", schema)
    assert found, f"CHECK {constraint} nicht in db/schema.sql gefunden"
    return re.compile(found.group(1).removeprefix("^").removesuffix("$"))


def _accepts(function: str, value: str) -> bool:
    try:
        _call(function, value)
    except ToolError:
        return False
    return True


def test_domain_rule_matches_the_schema_regex() -> None:
    """check_domain nimmt eine Domain genau dann an, wenn sie den CHECK von customers.domain erfüllt (nach lower())."""
    pattern = _schema_pattern("customers_domain_format", "domain")
    for domain in [*DOMAINS_VALID, *DOMAINS_INVALID, "Firma.Example", "MÜLLER.example"]:
        assert _accepts("check_domain", domain) == bool(pattern.fullmatch(domain.lower())), domain
    assert all(pattern.fullmatch(d) for d in DOMAINS_VALID), (
        "die Beispielliste ist nicht mehr gültig"
    )
    assert not any(pattern.fullmatch(d) for d in DOMAINS_INVALID), (
        "die Beispielliste ist nicht mehr ungültig"
    )


def test_email_rule_matches_the_schema_regex() -> None:
    """check_email nimmt eine Adresse genau dann an, wenn sie den CHECK von contacts.email erfüllt (nach lower()).

    Gilt für Adressen mit ASCII-Lokalteil. Das Schema erlaubt vor dem @ jedes Zeichen außer @ und Leerraum, check_email
    nur ASCII (siehe test_check_email_rejects_a_non_ascii_local_part_with_the_fixed_message).
    """
    pattern = _schema_pattern("contacts_email_format", "email")
    for domain in [*DOMAINS_VALID, *DOMAINS_INVALID]:
        email = f"kontakt@{domain}"
        assert _accepts("check_email", email) == bool(pattern.fullmatch(email.lower())), email
    for email in [
        "kontakt@firma.example",
        "@firma.example",
        "a@",
        "a@b@firma.example",
        "kontakt.firma.example",
    ]:
        assert _accepts("check_email", email) == bool(pattern.fullmatch(email.lower())), email


# Querschnitt


@pytest.mark.parametrize(("function", "message", "optional_none"), CHECKS)
def test_every_check_rejects_the_marker_without_echo(
    function: str, message: str, optional_none: bool
) -> None:
    assert_rejected(function, message, SECRET_MARKER + "x" * 3000)
    assert_rejected(function, message, "x" * 3000 + SECRET_MARKER)


@pytest.mark.parametrize(("function", "message", "optional_none"), CHECKS)
def test_every_check_rejects_non_strings_with_its_fixed_message(
    function: str, message: str, optional_none: bool
) -> None:
    values = NON_STRINGS if optional_none else [None, *NON_STRINGS]
    for value in values:
        assert_rejected(function, message, value)


def test_no_check_logs_anything_and_no_value_reaches_a_log(
    caplog: pytest.LogCaptureFixture, capsys: pytest.CaptureFixture[str]
) -> None:
    caplog.set_level(logging.DEBUG)
    samples = {
        "check_company_name": ["Hartmann GmbH", SECRET_MARKER + "x" * 200],
        "check_first_name": ["Erika", SECRET_MARKER + "x" * 100],
        "check_last_name": ["Muster", SECRET_MARKER + "x" * 100],
        "check_job_title": ["Leiter", SECRET_MARKER + "x" * 100],
        "check_email": ["kontakt@firma.example", SECRET_MARKER + "@firma.example"],
        "check_domain": ["firma.example", SECRET_MARKER + ".example"],
        "check_language": ["de", SECRET_MARKER],
        "check_country": ["de", SECRET_MARKER],
        "check_industry": ["food", SECRET_MARKER],
        "check_activity_type": ["note", SECRET_MARKER],
        "check_subject": ["Betreff", SECRET_MARKER + "x" * 200],
        "check_summary": ["Text", SECRET_MARKER + "x" * 2000],
    }
    for function, values in samples.items():
        for value in values:
            try:
                _call(function, value)
            except ToolError:
                pass
    logged = "\n".join(f"{r.getMessage()} {r.exc_text or ''}" for r in caplog.records)
    captured = capsys.readouterr()
    assert [r for r in caplog.records if r.name.startswith("hoffmann_data")] == []
    for text in (logged, captured.out, captured.err):
        assert SECRET_MARKER not in text


# Firmenname, Vorname, Nachname (einzeilig)

SINGLE_LINE_FIELDS = [
    pytest.param("check_company_name", "COMPANY_NAME_INVALID", 200, id="firma"),
    pytest.param("check_first_name", "FIRST_NAME_INVALID", 100, id="vorname"),
    pytest.param("check_last_name", "LAST_NAME_INVALID", 100, id="nachname"),
]


@pytest.mark.parametrize(("function", "message", "limit"), SINGLE_LINE_FIELDS)
def test_single_line_fields_accept_and_strip(function: str, message: str, limit: int) -> None:
    for value, expected in [
        ("Erika", "Erika"),
        ("  Erika  ", "Erika"),
        ("\n\tErika \r\n", "Erika"),
        ("x", "x"),
        ("x" * limit, "x" * limit),
        ("  " + "x" * limit + "  ", "x" * limit),
        ("ä" * limit, "ä" * limit),  # Zeichen zählen, nicht Bytes
        (EMOJI * limit, EMOJI * limit),
        ("e" + COMBINING_ACUTE, "e" + COMBINING_ACUTE),  # zwei Zeichen
        ("Jean-Luc O'Brien", "Jean-Luc O'Brien"),
        ("Robert'); DROP TABLE customers;--", "Robert'); DROP TABLE customers;--"),
    ]:
        assert _call(function, value) == expected


@pytest.mark.parametrize(("function", "message", "limit"), SINGLE_LINE_FIELDS)
def test_single_line_fields_reject_empty_and_too_long(
    function: str, message: str, limit: int
) -> None:
    for value in [
        "",
        "   ",
        "\n",
        "\t",
        " \n\t ",
        "x" * (limit + 1),
        " " + "x" * (limit + 1) + " ",
        "ä" * (limit + 1),
        EMOJI * (limit + 1),
        ("e" + COMBINING_ACUTE) * (limit // 2 + 1),  # mehr als limit Zeichen
        SECRET_MARKER + "x" * limit,
    ]:
        assert_rejected(function, message, value)


@pytest.mark.parametrize(("function", "message", "limit"), SINGLE_LINE_FIELDS)
@pytest.mark.parametrize(
    "character", SINGLE_LINE_REJECTED.values(), ids=SINGLE_LINE_REJECTED.keys()
)
def test_single_line_fields_reject_control_format_and_separator_characters(
    function: str, message: str, limit: int, character: str
) -> None:
    assert_rejected(function, message, f"vor{character}nach")
    assert_rejected(function, message, character)


# Position (optional)


def test_job_title_accepts_missing_empty_and_valid_values() -> None:
    from hoffmann_data.limits import check_job_title

    assert check_job_title(None) is None
    assert check_job_title("") is None
    assert check_job_title("   ") is None
    assert check_job_title("\n\t") is None
    assert check_job_title("Einkaufsleiter") == "Einkaufsleiter"
    assert check_job_title("  Leiter  ") == "Leiter"
    assert check_job_title("x" * 100) == "x" * 100
    assert check_job_title("ä" * 100) == "ä" * 100
    assert check_job_title(EMOJI * 100) == EMOJI * 100
    assert check_job_title("  " + "x" * 100 + "  ") == "x" * 100


@pytest.mark.parametrize(
    "value",
    [
        "x" * 101,
        "ä" * 101,
        EMOJI * 101,
        " " + "x" * 101 + " ",
        SECRET_MARKER + "x" * 100,
        *[f"vor{c}nach" for c in SINGLE_LINE_REJECTED.values()],
        *[c for c in CONTROL_CHARACTERS.values() if not c.isspace()],
    ],
)
def test_job_title_rejects_too_long_and_control_characters(value: str) -> None:
    assert_rejected("check_job_title", "JOB_TITLE_INVALID", value)


@pytest.mark.parametrize("value", [123, 1.5, True, False, ["Leiter"], {"a": 1}, b"abc"])
def test_job_title_rejects_non_strings_but_not_none(value: Any) -> None:
    assert_rejected("check_job_title", "JOB_TITLE_INVALID", value)


# E-Mail


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("kontakt@firma.example", "kontakt@firma.example"),
        ("  Kontakt@Firma.Example  ", "kontakt@firma.example"),
        ("\nkontakt@firma.example\r\n", "kontakt@firma.example"),
        ("Max.Muster@FIRMA.EXAMPLE", "max.muster@firma.example"),
        ("a@b.example", "a@b.example"),
        ("a@mail.firma.example", "a@mail.firma.example"),
        ("max.mustermann+tag@firma-x.example", "max.mustermann+tag@firma-x.example"),
        ("a" * 240 + "@firma.example", "a" * 240 + "@firma.example"),  # genau 254
        ("  " + "a" * 240 + "@firma.example" + "  ", "a" * 240 + "@firma.example"),
        ("kontakt@xn--mller-kva.example", "kontakt@xn--mller-kva.example"),
    ],
    ids=[
        "normal",
        "gross-und-rand",
        "umbruch-aussen",
        "lokalteil-gross",
        "kurz",
        "subdomain",
        "plus-und-punkt",
        "genau-254",
        "254-nach-strip",
        "punycode",
    ],
)
def test_check_email_accepts_and_lowercases_the_whole_address(value: str, expected: str) -> None:
    from hoffmann_data.limits import check_email

    assert check_email(value) == expected


EMAIL_REJECTED = [
    "",
    "   ",
    "a" * 241 + "@firma.example",  # 255
    "ä" * 241 + "@firma.example",
    " " + "a" * 241 + "@firma.example" + " ",
    "kontakt.firma.example",
    "kontakt(at)firma.example",
    "a@b@firma.example",
    "@firma.example",
    "a@",
    "@",
    "a@.example",
    "a@example",
    "a@firma.de",
    "a@firma.example.com",
    "a@firma..example",
    "a@fir_ma.example",
    "a@müller.example",
    "a@firma example",
    "a b@firma.example",
    "a\tb@firma.example",
    "Max Muster <a@firma.example>",
    "<a@firma.example>",
    "a@firma.example,",
    "a" + NO_BREAK_SPACE + "b@firma.example",
    "a" + EM_SPACE + "b@firma.example",
    "kontakt@firma.example" + NUL,
    NUL + "kontakt@firma.example",
    # Der Marker ist nach lower() ein schema-gültiger Lokalteil bzw. ein gültiges Domain-Label,
    # deshalb enden diese Werte auf .de und bleiben aus diesem Grund ungültig
    SECRET_MARKER + "@firma.de",
    "kontakt@" + SECRET_MARKER + ".de",
]


@pytest.mark.parametrize("value", EMAIL_REJECTED, ids=repr)
def test_check_email_rejects_with_the_fixed_message(value: str) -> None:
    assert_rejected("check_email", "EMAIL_INVALID", value)


# Nur ASCII vor dem @: Das lower() von Python und das von PostgreSQL unterscheiden sich bei diesen Zeichen (und das
# von PostgreSQL hängt von der Locale ab). Das Kelvin-Zeichen macht Python zu einem ASCII-k.
NON_ASCII_LOCAL_PARTS = {
    "umlaut": "m" + chr(0xFC) + "ller",
    "umlaute-254": chr(0xE4) * 240,  # Länge der Adresse genau 254: nur Nicht-ASCII ist der Grund
    "kelvin": chr(0x212A) + "ontakt",
    "i-mit-punkt": "kontakt" + chr(0x130),
    "scharfes-s-gross": "stra" + chr(0x1E9E) + "e",
    "andere-schrift": chr(0x4E2D) + chr(0x6587),
}


@pytest.mark.parametrize("local", NON_ASCII_LOCAL_PARTS.values(), ids=NON_ASCII_LOCAL_PARTS.keys())
def test_check_email_rejects_a_non_ascii_local_part_with_the_fixed_message(local: str) -> None:
    assert_rejected("check_email", "EMAIL_INVALID", f"{local}@firma.example")
    assert_rejected("check_email", "EMAIL_INVALID", f"  {local.upper()}@FIRMA.EXAMPLE  ")


def test_check_email_does_not_echo_a_non_ascii_local_part() -> None:
    """Der Marker steht im Lokalteil; die feste Meldung nennt weder ihn noch die Adresse."""
    value = SECRET_MARKER + chr(0xFC) + "x" * 40 + "@firma.example"
    assert_rejected("check_email", "EMAIL_INVALID", value)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("Max.Muster@Firma.Example", "max.muster@firma.example"),
        ("MAX_MUSTER@firma.example", "max_muster@firma.example"),
        ("Max-Muster@firma.example", "max-muster@firma.example"),
        ("Max+Tag@firma.example", "max+tag@firma.example"),
        ("A.B_C-D+E@firma.example", "a.b_c-d+e@firma.example"),
    ],
    ids=["punkt", "unterstrich", "bindestrich", "plus", "alle-zusammen"],
)
def test_check_email_keeps_an_ascii_local_part_valid_and_lowercases_it(
    value: str, expected: str
) -> None:
    from hoffmann_data.limits import check_email

    assert check_email(value) == expected


@pytest.mark.parametrize(
    "character", SINGLE_LINE_REJECTED.values(), ids=SINGLE_LINE_REJECTED.keys()
)
def test_check_email_rejects_control_format_and_separator_characters_everywhere(
    character: str,
) -> None:
    assert_rejected("check_email", "EMAIL_INVALID", f"a{character}b@firma.example")
    assert_rejected("check_email", "EMAIL_INVALID", f"a@fir{character}ma.example")
    assert_rejected("check_email", "EMAIL_INVALID", f"a@firma.example{character}x")


def test_email_domain_is_the_part_after_the_at_sign() -> None:
    from hoffmann_data.limits import check_email, email_domain

    assert email_domain("kontakt@firma.example") == "firma.example"
    assert email_domain("a@mail.firma.example") == "mail.firma.example"
    assert email_domain(check_email("  Kontakt@Firma.Example ")) == "firma.example"


# Domain (log_activity und Domain der Adresse)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        *[(d, d) for d in DOMAINS_VALID],
        ("  Firma.Example  ", "firma.example"),
        ("FIRMA.EXAMPLE", "firma.example"),
        ("\nfirma.example\r\n", "firma.example"),
        ("a" * 245 + ".example", "a" * 245 + ".example"),  # genau 253
    ],
    ids=repr,
)
def test_check_domain_accepts_and_lowercases(value: str, expected: str) -> None:
    from hoffmann_data.limits import check_domain

    assert check_domain(value) == expected


@pytest.mark.parametrize(
    "value",
    [
        "",
        "   ",
        "a" * 246 + ".example",  # 254
        " " + "a" * 246 + ".example" + " ",
        *DOMAINS_INVALID,
        "kontakt@firma.example",  # eine Adresse ist keine Domain
        "firma.example" + NUL,
        "fir" + chr(0x200B) + "ma.example",
        "fir" + chr(0xD800) + "ma.example",
        "fir\nma.example",
        "firma." + "\t" + "example",
        "MÜLLER.EXAMPLE",
        # Der Marker ist nach lower() ein gültiges Domain-Label; .de macht die Domain ungültig
        SECRET_MARKER + ".de",
        SECRET_MARKER,
    ],
    ids=repr,
)
def test_check_domain_rejects_with_the_fixed_message(value: str) -> None:
    assert_rejected("check_domain", "DOMAIN_INVALID", value)


# Sprache, Land, Branche, Aktivitätstyp (exakt, klein)

ENUM_WRONG_FORMS = ["", "   ", "\n", NUL, SECRET_MARKER, "x" * 300]


@pytest.mark.parametrize(
    ("function", "message", "valid", "wrong"),
    [
        pytest.param(
            "check_language",
            "LANGUAGE_INVALID",
            ["de", "en"],
            [
                "DE",
                "En",
                " de",
                "de ",
                "de\n",
                "d\x00e",
                "fr",
                "deutsch",
                "en-US",
                *ENUM_WRONG_FORMS,
            ],
            id="sprache",
        ),
        pytest.param(
            "check_country",
            "COUNTRY_INVALID",
            ["de", "at", "ch"],
            [
                "DE",
                "Ch",
                " at",
                "ch ",
                "at\n",
                "fr",
                "deu",
                "de-AT",
                "Deutschland",
                *ENUM_WRONG_FORMS,
            ],
            id="land",
        ),
        pytest.param(
            "check_industry",
            "INDUSTRY_INVALID",
            ["automotive", "food", "packaging", "metalworking", "plastics", "logistics", "other"],
            [
                "Automotive",
                "FOOD",
                " food",
                "other ",
                "food\n",
                "banking",
                "auto",
                "Maschinenbau",
                *ENUM_WRONG_FORMS,
            ],
            id="branche",
        ),
        pytest.param(
            "check_activity_type",
            "ACTIVITY_TYPE_INVALID",
            ["inquiry", "complaint", "note"],
            [
                "call",
                "meeting",
                "order",
                "email",
                "quote",
                "service",
                "Inquiry",
                "INQUIRY",
                "Complaint",
                "Note",
                " note",
                "note ",
                "inquiry\n",
                "note\x00",
                *ENUM_WRONG_FORMS,
            ],
            id="aktivitaetstyp",
        ),
    ],
)
def test_enumerations_accept_only_the_exact_values(
    function: str, message: str, valid: list[str], wrong: list[str]
) -> None:
    for value in valid:
        assert _call(function, value) == value
    for value in wrong:
        assert_rejected(function, message, value)


def test_country_is_required() -> None:
    assert_rejected("check_country", "COUNTRY_INVALID", None)


def test_language_is_required() -> None:
    assert_rejected("check_language", "LANGUAGE_INVALID", None)


def test_activity_type_is_required() -> None:
    assert_rejected("check_activity_type", "ACTIVITY_TYPE_INVALID", None)


def test_industry_is_optional_and_defaults_to_other() -> None:
    from hoffmann_data.limits import DEFAULT_INDUSTRY, check_industry

    assert DEFAULT_INDUSTRY == "other"
    assert check_industry(None) == "other"
    # Nur ein fehlender Wert ergibt den Standard; ein ausdrücklich leerer Wert ist ungültig
    assert_rejected("check_industry", "INDUSTRY_INVALID", "")
    assert_rejected("check_industry", "INDUSTRY_INVALID", "   ")


# Betreff und Zusammenfassung (mehrzeilig)

MULTI_LINE_FIELDS = [
    pytest.param("check_subject", "SUBJECT_INVALID", 200, id="betreff"),
    pytest.param("check_summary", "SUMMARY_INVALID", 2000, id="zusammenfassung"),
]


@pytest.mark.parametrize(("function", "message", "limit"), MULTI_LINE_FIELDS)
def test_text_fields_accept_and_strip(function: str, message: str, limit: int) -> None:
    for value, expected in [
        ("Anfrage Gurtförderer", "Anfrage Gurtförderer"),
        ("  Betreff  ", "Betreff"),
        ("x", "x"),
        ("x" * limit, "x" * limit),
        ("  " + "x" * limit + "  ", "x" * limit),
        ("\n\n" + "x" * limit + "\n\n", "x" * limit),  # Ränder entfernt, danach genau am Limit
        ("ä" * limit, "ä" * limit),
        (EMOJI * limit, EMOJI * limit),
        ("zwei\nZeilen", "zwei\nZeilen"),
        ("mit\tTab", "mit\tTab"),
        ("mit\r\nCRLF", "mit\r\nCRLF"),
        ("a\n" * (limit // 2), "a\n" * (limit // 2 - 1) + "a"),  # Rand-Umbruch wird entfernt
        ("Robert'); DROP TABLE activities;--", "Robert'); DROP TABLE activities;--"),
    ]:
        assert _call(function, value) == expected


@pytest.mark.parametrize(("function", "message", "limit"), MULTI_LINE_FIELDS)
def test_text_fields_reject_empty_and_too_long(function: str, message: str, limit: int) -> None:
    for value in [
        "",
        "   ",
        "\n\t  ",
        "\r\n",
        "x" * (limit + 1),
        " " + "x" * (limit + 1) + " ",
        "ä" * (limit + 1),
        EMOJI * (limit + 1),
        "x\n" * limit,  # mehr als limit Zeichen
        SECRET_MARKER + "x" * limit,
        "x" * limit + SECRET_MARKER,
    ]:
        assert_rejected(function, message, value)


@pytest.mark.parametrize(("function", "message", "limit"), MULTI_LINE_FIELDS)
@pytest.mark.parametrize("character", MULTI_LINE_REJECTED.values(), ids=MULTI_LINE_REJECTED.keys())
def test_text_fields_reject_control_format_and_separator_characters(
    function: str, message: str, limit: int, character: str
) -> None:
    assert_rejected(function, message, f"vor{character}nach")
    # Am Rand entfernt strip() Zeichen, die als Leerraum gelten (z. B. U+0085, U+2028): nur die übrigen prüfen
    if not character.isspace():
        assert_rejected(function, message, "x" + character)
        assert_rejected(function, message, character + "x")


@pytest.mark.parametrize(("function", "message", "limit"), MULTI_LINE_FIELDS)
def test_text_fields_reject_nul_everywhere(function: str, message: str, limit: int) -> None:
    """PostgreSQL kann NUL nicht speichern: am Anfang, in der Mitte, am Ende und allein."""
    for value in [NUL, NUL + "x", "x" + NUL, "x" + NUL + "y", "x\n" + NUL + "\ny"]:
        assert_rejected(function, message, value)


@pytest.mark.parametrize(("function", "message", "limit"), MULTI_LINE_FIELDS)
def test_text_fields_allow_line_breaks_and_tabs_only_in_the_allowed_forms(
    function: str, message: str, limit: int
) -> None:
    assert _call(function, "a\nb\tc\r\nd") == "a\nb\tc\r\nd"
    for character in (chr(0x0B), chr(0x0C), chr(0x1C), chr(0x85), chr(0x2028), chr(0x2029)):
        assert_rejected(function, message, f"a{character}b")


# Querschnitt: gültige Werte kommen bereinigt, sonst unverändert zurück


def test_valid_values_are_returned_stripped_and_otherwise_unchanged() -> None:
    limits = _limits()
    assert limits.check_company_name("  Müller & Söhne GmbH ") == "Müller & Söhne GmbH"
    assert limits.check_first_name(" ERIKA ") == "ERIKA"  # Schreibung bleibt erhalten
    assert limits.check_last_name("von der Heide") == "von der Heide"
    assert limits.check_job_title("  Leiter EINKAUF ") == "Leiter EINKAUF"
    assert limits.check_subject(" Anfrage: GURTband ") == "Anfrage: GURTband"
    assert limits.check_summary("\nErste Zeile\nZweite ZEILE\n") == "Erste Zeile\nZweite ZEILE"
    # Nur E-Mail und Domain werden klein
    assert limits.check_email("Erika.Muster@Firma.Example") == "erika.muster@firma.example"
    assert limits.check_domain("Firma.Example") == "firma.example"
