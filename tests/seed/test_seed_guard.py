"""Tests des Löschschutzes db/seed/guard.py (F05, ohne Datenbank).

Geprüft werden: Ziel aus der URL (alle Abbruchfälle mit genauer Meldung), der tatsächlich
verbundene Host, die Bestätigung SEED_CONFIRM_RESET, die Ausgabe vor dem Löschen und die festen
DROP-Befehle. Dazu: Keine Meldung und kein Traceback enthält Passwort, Benutzer oder URL, und das
Modul liest nirgends die Umgebung.
"""

import re
import traceback
from dataclasses import FrozenInstanceError
from pathlib import Path

import psycopg
import pytest
from psycopg import conninfo

from db.seed import guard
from db.seed.guard import (
    RESET_TABLES,
    SeedGuardError,
    Target,
    check_reset_confirmation,
    describe_target,
    drop_statements,
    parse_target,
    verify_effective_host,
)

PASSWORD = "Geheim#123"
ENCODED = "Geheim%23123"
USER = "benutzer_x"
BASE = f"postgresql://{USER}:{ENCODED}@Db.Example.com:5432/postgres"
TARGET = Target(host="db.example.com", dbname="postgres")
EXISTING = {"products": 40, "customers": 21}

EMPTY = "DATABASE_URL fehlt oder ist leer."
BAD_SCHEME = "DATABASE_URL muss mit postgresql:// beginnen."
UNREADABLE = "DATABASE_URL ist keine lesbare URL."
HASH = (
    "DATABASE_URL enthält ein #. Sonderzeichen im Passwort müssen prozentkodiert werden, "
    "zum Beispiel %23."
)
COMMA = "DATABASE_URL darf nur einen Host enthalten (kein Komma in der Adresse)."
NO_HOST = "DATABASE_URL enthält keinen Host (Unix-Sockets sind nicht erlaubt)."
NO_DBNAME = "DATABASE_URL enthält keinen Datenbanknamen."
DBNAME_PARAM = (
    "DATABASE_URL darf den Parameter dbname nicht enthalten: "
    "Er würde die angezeigte Datenbank überstimmen."
)
MULTI_AT = "DATABASE_URL enthält mehr als ein @. Ein @ im Passwort muss als %40 geschrieben werden."
AT_AFTER_QUERY = (
    "DATABASE_URL hat einen unklaren Aufbau: Ein @ steht hinter einem ? vor dem ersten /."
)
HOST_ENCODED = "DATABASE_URL darf im Host keine Prozentkodierung enthalten."
NON_PRINTABLE = (
    "DATABASE_URL darf nur sichtbare ASCII-Zeichen enthalten (Leerzeichen, Steuerzeichen "
    "und Sonderzeichen prozentkodieren)."
)


def _params(names: str) -> str:
    return (
        f"DATABASE_URL darf die Parameter {names} nicht enthalten: "
        "Sie würden den geprüften Host überstimmen."
    )


def _env(names: str) -> str:
    return (
        f"Die Umgebungsvariable(n) {names} würden den geprüften Host überstimmen "
        "und dürfen nicht gesetzt sein."
    )


# parse_target: gültige URLs


VALID_CASES = [
    (BASE, "db.example.com", "postgres"),
    (BASE + "?sslmode=require", "db.example.com", "postgres"),
    (
        "postgresql://postgres.abcdefgh:pw@db.example.com:6543/postgres",
        "db.example.com",
        "postgres",
    ),
    (
        "postgresql://u:p%40ss%3Aw%2Frd%23%2C%25@db.example.com/postgres",
        "db.example.com",
        "postgres",
    ),
    ("postgresql://u:a%2Cb@db.example.com/postgres", "db.example.com", "postgres"),
    ("postgresql://u:a,b@db.example.com/postgres", "db.example.com", "postgres"),
    ("postgresql://u:p@[2001:DB8::1]:5432/postgres", "2001:db8::1", "postgres"),
    ("postgresql://u@[::1]/db", "::1", "db"),
    ("postgresql://u:p@192.0.2.10:5432/postgres", "192.0.2.10", "postgres"),
    ("postgresql://db.example.com/postgres", "db.example.com", "postgres"),
    ("postgres://db.example.com/postgres", "db.example.com", "postgres"),
    ("postgresql://DB.EXAMPLE.COM/postgres", "db.example.com", "postgres"),
    ("postgresql://db.example.com/my%20db", "db.example.com", "my db"),
]
VALID_IDS = [
    "mit-passwort",
    "mit-sslmode",
    "supabase-benutzer",
    "sonderzeichen-prozentkodiert",
    "komma-prozentkodiert",
    "komma-im-passwort",
    "ipv6-mit-port",
    "ipv6-ohne-port",
    "ipv4",
    "ohne-anmeldung",
    "schema-postgres",
    "grossschreibung",
    "datenbankname-dekodiert",
]


@pytest.mark.parametrize(("url", "host", "dbname"), VALID_CASES, ids=VALID_IDS)
def test_parse_target_valid(url, host, dbname):
    assert parse_target(url, {}) == Target(host=host, dbname=dbname)


def test_target_has_only_host_and_dbname_and_is_frozen():
    target = parse_target(BASE, {})
    assert repr(target) == "Target(host='db.example.com', dbname='postgres')"
    for secret in (PASSWORD, ENCODED, "Geheim", USER):
        assert secret not in repr(target)
    with pytest.raises(FrozenInstanceError):
        target.host = "anders"


def test_percent_encoded_at_in_the_password_is_allowed():
    target = parse_target("postgresql://u:p%40ss@db.example.com/postgres", {})
    assert target == Target(host="db.example.com", dbname="postgres")
    target = parse_target("postgresql://u:%40@db.example.com/postgres", {})
    assert target.host == "db.example.com"


def test_other_pg_variables_are_not_a_problem():
    environ = {"PGHOST": "anderer.example", "PGDATABASE": "x", "PGPASSWORD": "y"}
    assert parse_target(BASE, environ).host == "db.example.com"


def test_real_os_environment_is_not_read(monkeypatch):
    monkeypatch.setenv("PGHOSTADDR", "192.0.2.1")
    monkeypatch.setenv("PGSERVICE", "mein-dienst")
    assert parse_target(BASE, {}).host == "db.example.com"


def test_guard_source_does_not_touch_the_environment():
    source = Path(guard.__file__).read_text(encoding="utf-8")
    for forbidden in ("import os", "from os", "os.environ", "getenv"):
        assert forbidden not in source


# parse_target: Abbruchfälle (jeder mit genau der erwarteten Meldung)

DB = "postgresql://u:p@db.example.com/postgres"

PARSE_ERRORS = [
    ("none", None, {}, EMPTY),
    ("leer", "", {}, EMPTY),
    ("leerzeichen", "   ", {}, EMPTY),
    ("schluessel-wert-form", "host=db.example.com dbname=postgres", {}, BAD_SCHEME),
    ("anderes-schema", "mysql://u:p@db.example.com/postgres", {}, BAD_SCHEME),
    ("schema-grossgeschrieben", "POSTGRESQL://db.example.com/postgres", {}, BAD_SCHEME),
    ("fuehrendes-leerzeichen", " postgresql://db.example.com/postgres", {}, BAD_SCHEME),
    ("zeilenumbruch-im-host", "postgresql://db.exam\nple.com/postgres", {}, NON_PRINTABLE),
    ("zeilenumbruch-am-ende", "postgresql://db.example.com/postgres\n", {}, NON_PRINTABLE),
    ("leerzeichen-im-namen", "postgresql://db.example.com/post gres", {}, NON_PRINTABLE),
    ("nicht-ascii-im-host", "postgresql://db.exämple.com/postgres", {}, NON_PRINTABLE),
    ("kaputte-ipv6-klammer", "postgresql://u:p@[::1/postgres", {}, UNREADABLE),
    ("datenbankname-kein-utf8", "postgresql://db.example.com/%ff", {}, UNREADABLE),
    ("mehrere-at", "postgresql://u:p@ss@db.example.com/postgres", {}, MULTI_AT),
    ("mehrere-at-leere-anmeldung", "postgresql://@@db.example.com/postgres", {}, MULTI_AT),
    ("at-vor-parameter", "postgresql://u:p@db.example.com?x=y@z/postgres", {}, MULTI_AT),
    ("at-nach-fragezeichen", "postgresql://h.example?x=y@db.example.com/db", {}, AT_AFTER_QUERY),
    ("host-prozentkodiert", "postgresql://u:p@lo%63alhost/db", {}, HOST_ENCODED),
    (
        "host-prozentkodiertes-at",
        "postgresql://u:p@evil.example%40db.example.com/db",
        {},
        HOST_ENCODED,
    ),
    ("port-kein-zahl", "postgresql://u:p@db.example.com:abc/postgres", {}, UNREADABLE),
    ("port-zu-gross", "postgresql://u:p@db.example.com:99999/postgres", {}, UNREADABLE),
    ("raute-im-passwort", f"postgresql://u:{PASSWORD}@db.example.com/postgres", {}, HASH),
    ("raute-fragment", "postgresql://db.example.com/postgres#abschnitt", {}, HASH),
    ("zwei-hosts", "postgresql://u:p@h1.example,h2.example/postgres", {}, COMMA),
    ("zwei-hosts-mit-port", "postgresql://u:p@h1.example:1,h2.example:2/postgres", {}, COMMA),
    ("socket-ohne-netzwerkteil", "postgresql:///postgres", {}, NO_HOST),
    ("socket-mit-anmeldung", "postgresql://u:p@/postgres", {}, NO_HOST),
    ("socket-mit-host-parameter", "postgresql:///postgres?host=/var/run/postgresql", {}, NO_HOST),
    ("parameter-host", DB + "?host=anderer.example", {}, _params("host")),
    ("parameter-hostaddr", DB + "?hostaddr=192.0.2.1", {}, _params("hostaddr")),
    ("parameter-service", DB + "?service=mein-dienst", {}, _params("service")),
    ("parameter-host-leer", DB + "?host=", {}, _params("host")),
    ("parameter-host-ohne-gleichheitszeichen", DB + "?host", {}, _params("host")),
    ("parameter-grossschreibung", DB + "?HOST=anderer.example", {}, _params("host")),
    ("parameter-nach-anderem", DB + "?sslmode=require&host=x", {}, _params("host")),
    ("parameter-zwei", DB + "?service=a&host=b", {}, _params("host, service")),
    (
        "parameter-drei-sortiert",
        DB + "?service=a&hostaddr=b&host=c",
        {},
        _params("host, hostaddr, service"),
    ),
    ("parameter-dbname", DB + "?dbname=andere", {}, DBNAME_PARAM),
    ("host-vor-dbname", DB + "?dbname=a&host=b", {}, _params("host")),
    ("ohne-datenbankname", "postgresql://u:p@db.example.com", {}, NO_DBNAME),
    ("nur-schraegstrich", "postgresql://u:p@db.example.com/", {}, NO_DBNAME),
    ("umgebung-hostaddr", DB, {"PGHOSTADDR": "192.0.2.1"}, _env("PGHOSTADDR")),
    ("umgebung-service", DB, {"PGSERVICE": "mein-dienst"}, _env("PGSERVICE")),
    ("umgebung-leer", DB, {"PGSERVICE": ""}, _env("PGSERVICE")),
    ("umgebung-beide", DB, {"PGSERVICE": "a", "PGHOSTADDR": "b"}, _env("PGHOSTADDR, PGSERVICE")),
]


@pytest.mark.parametrize(
    ("url", "environ", "message"),
    [case[1:] for case in PARSE_ERRORS],
    ids=[case[0] for case in PARSE_ERRORS],
)
def test_parse_target_errors(url, environ, message):
    with pytest.raises(SeedGuardError) as excinfo:
        parse_target(url, environ)
    assert str(excinfo.value) == message


# Keine Geheimnisse in Meldungen, repr und Traceback

SECRET_CASES = [
    ("leer", "", {}),
    ("anderes-schema", f"mysql://{USER}:{ENCODED}@db.example.com/postgres", {}),
    ("schema-grossgeschrieben", f"POSTGRESQL://{USER}:{ENCODED}@db.example.com/postgres", {}),
    ("fuehrendes-leerzeichen", f" postgresql://{USER}:{ENCODED}@db.example.com/postgres", {}),
    ("zeilenumbruch", f"postgresql://{USER}:{ENCODED}@db.example.com/postgres\n", {}),
    ("nicht-ascii", f"postgresql://{USER}:{ENCODED}@db.exämple.com/postgres", {}),
    ("mehrere-at", f"postgresql://{USER}:{ENCODED}@ss@db.example.com/postgres", {}),
    ("mehrere-at-roh", f"postgresql://{USER}:Geheim@123@db.example.com/postgres", {}),
    ("at-nach-fragezeichen", f"postgresql://{USER}?x={ENCODED}@db.example.com/postgres", {}),
    ("host-prozentkodiert", f"postgresql://{USER}:{ENCODED}@lo%63alhost/postgres", {}),
    ("datenbankname-kein-utf8", f"postgresql://{USER}:{ENCODED}@db.example.com/%ff", {}),
    ("kaputte-ipv6-klammer", f"postgresql://{USER}:{ENCODED}@[::1/postgres", {}),
    ("port-kein-zahl", f"postgresql://{USER}:{ENCODED}@db.example.com:abc/postgres", {}),
    ("raute-roh", f"postgresql://{USER}:{PASSWORD}@db.example.com/postgres", {}),
    ("raute-mit-ziffern", f"postgresql://{USER}:123#x@db.example.com/postgres", {}),
    ("zwei-hosts", f"postgresql://{USER}:{ENCODED}@h1.example,h2.example/postgres", {}),
    ("socket", f"postgresql://{USER}:{ENCODED}@/postgres", {}),
    ("parameter-host", f"postgresql://{USER}:{ENCODED}@db.example.com/postgres?host=x", {}),
    ("parameter-hostaddr", f"postgresql://{USER}:{ENCODED}@db.example.com/postgres?hostaddr=x", {}),
    ("parameter-service", f"postgresql://{USER}:{ENCODED}@db.example.com/postgres?service=x", {}),
    ("parameter-dbname", f"postgresql://{USER}:{ENCODED}@db.example.com/postgres?dbname=x", {}),
    ("ohne-datenbankname", f"postgresql://{USER}:{ENCODED}@db.example.com", {}),
    ("umgebung", BASE, {"PGSERVICE": PASSWORD, "PGHOSTADDR": ENCODED}),
]


def _assert_no_secrets(exc: BaseException, url: str | None) -> None:
    texts = [str(exc), repr(exc), "".join(traceback.format_exception(exc))]
    secrets = [PASSWORD, ENCODED, "Geheim", USER]
    if url:
        secrets.append(url)
    for text in texts:
        for secret in secrets:
            assert secret not in text
    assert exc.__cause__ is None
    assert exc.__context__ is None


@pytest.mark.parametrize(
    ("url", "environ"),
    [case[1:] for case in SECRET_CASES],
    ids=[case[0] for case in SECRET_CASES],
)
def test_parse_errors_never_contain_secrets(url, environ):
    with pytest.raises(SeedGuardError) as excinfo:
        parse_target(url, environ)
    _assert_no_secrets(excinfo.value, url)


def test_confirmation_error_never_contains_the_given_value():
    with pytest.raises(SeedGuardError) as excinfo:
        check_reset_confirmation(TARGET, BASE, EXISTING)
    _assert_no_secrets(excinfo.value, BASE)


def test_host_error_never_contains_secrets():
    with pytest.raises(SeedGuardError) as excinfo:
        verify_effective_host(parse_target(BASE, {}), f"{PASSWORD}@db.example.com")
    _assert_no_secrets(excinfo.value, BASE)


def test_description_never_contains_secrets():
    target = parse_target(BASE, {})
    text = describe_target(target, "public", EXISTING)
    for secret in (PASSWORD, ENCODED, "Geheim", USER, BASE):
        assert secret not in text


# verify_effective_host


@pytest.mark.parametrize("effective", ["db.example.com", "DB.Example.COM", "Db.example.com"])
def test_effective_host_matches(effective):
    assert verify_effective_host(TARGET, effective) is None


@pytest.mark.parametrize(
    "effective",
    [
        "other.example.com",
        "db.example.com.evil.example",
        "evil-db.example.com",
        "db.example",
        "db.example.com ",
        "192.0.2.10",
    ],
)
def test_effective_host_differs(effective):
    with pytest.raises(SeedGuardError) as excinfo:
        verify_effective_host(TARGET, effective)
    assert str(excinfo.value) == EFFECTIVE_MESSAGE


EFFECTIVE_MESSAGE = (
    'Die Verbindung geht an einen anderen Host als die URL ("db.example.com"). '
    "Abbruch, es wurde nichts verändert."
)


@pytest.mark.parametrize(
    "effective",
    [
        "Geheim@db.example.com",
        f"{USER}:{ENCODED}@db.example.com",
        f"{USER}:{PASSWORD}@db.example.com",
        "Geheim",
        "evil.example",
    ],
)
def test_effective_host_is_never_put_into_the_message(effective):
    # Der Wert kommt von libpq und kann bei einem nicht kodierten @ im Passwort Passwortteile
    # enthalten. Nur der geprüfte Ziel-Host aus der URL darf in der Meldung stehen.
    with pytest.raises(SeedGuardError) as excinfo:
        verify_effective_host(TARGET, effective)
    exc = excinfo.value
    assert str(exc) == EFFECTIVE_MESSAGE
    for text in (str(exc), repr(exc), "".join(traceback.format_exception(exc))):
        assert effective not in text
        assert "Geheim" not in text
        assert USER not in text
    assert exc.__cause__ is None
    assert exc.__context__ is None


@pytest.mark.parametrize("effective", [None, ""])
def test_effective_host_unknown(effective):
    with pytest.raises(SeedGuardError) as excinfo:
        verify_effective_host(TARGET, effective)
    assert str(excinfo.value) == (
        "Der tatsächlich verwendete Host der Verbindung ist unbekannt. "
        "Abbruch, es wurde nichts verändert."
    )


# check_reset_confirmation

CONFIRM_MESSAGE = (
    'Im Ziel "db.example.com" (Datenbank postgres) sind bereits Tabellen vorhanden. '
    'Zum Überschreiben muss SEED_CONFIRM_RESET genau auf den Host "db.example.com" '
    "gesetzt sein (nicht yes, ohne Port, ohne Leerzeichen). Es wurde nichts gelöscht."
)


@pytest.mark.parametrize("value", [None, "", "yes", "falsch", BASE])
def test_fresh_database_needs_no_confirmation(value):
    assert check_reset_confirmation(TARGET, value, {}) is None


@pytest.mark.parametrize("value", ["db.example.com", "DB.EXAMPLE.COM", "Db.Example.Com"])
def test_correct_confirmation_passes(value):
    assert check_reset_confirmation(TARGET, value, EXISTING) is None


@pytest.mark.parametrize(
    "value",
    [
        None,
        "",
        "yes",
        "true",
        "1",
        "other.example.com",
        " db.example.com",
        "db.example.com ",
        "db.example.com\n",
        "\tdb.example.com",
        "db.example.com:5432",
        "example.com",
        "db.example.co",
        "db.example",
        "db",
        "evil.db.example.com",
        "evil-db.example.com",
        "db.example.com.evil.example",
        BASE,
        "postgresql://db.example.com/postgres",
        5,
        True,
        ["db.example.com"],
    ],
    ids=lambda value: repr(value)[:40],
)
def test_wrong_confirmation_fails(value):
    with pytest.raises(SeedGuardError) as excinfo:
        check_reset_confirmation(TARGET, value, EXISTING)
    assert str(excinfo.value) == CONFIRM_MESSAGE


def test_existing_table_without_rows_still_needs_confirmation():
    with pytest.raises(SeedGuardError):
        check_reset_confirmation(TARGET, None, {"products": 0})
    assert check_reset_confirmation(TARGET, "db.example.com", {"products": 0}) is None


def test_confirmation_message_names_host_and_variable():
    with pytest.raises(SeedGuardError) as excinfo:
        check_reset_confirmation(TARGET, "yes", EXISTING)
    message = str(excinfo.value)
    assert '"db.example.com"' in message
    assert "SEED_CONFIRM_RESET" in message
    assert "yes" not in message.replace("nicht yes", "")


# describe_target


def test_description_contains_target_tables_and_hint():
    existing = {
        "products": 40,
        "product_fits": 24,
        "customers": 20,
        "contacts": 35,
        "activities": 77,
        "discount_rules": 1,
    }
    text = describe_target(TARGET, "public", existing)
    assert "\n" in text
    assert "Host:      db.example.com" in text
    assert "Datenbank: postgres" in text
    assert "Schema:    public" in text
    for line in (
        "activities: 77 Zeilen",
        "product_fits: 24 Zeilen",
        "contacts: 35 Zeilen",
        "discount_rules: 1 Zeile",
        "customers: 20 Zeilen",
        "products: 40 Zeilen",
    ):
        assert line in text
    assert "1 Zeilen" not in text
    assert "später vom Agenten und von Mitarbeitenden" in text
    assert "created_by agent oder staff" in text


def test_description_lists_tables_in_drop_order_then_others_sorted():
    existing = {"zzz_extra": 3, "products": 1, "aaa_extra": 2, "activities": 5}
    text = describe_target(TARGET, "public", existing)
    positions = [
        text.index(f"  {name}: ") for name in ("activities", "products", "aaa_extra", "zzz_extra")
    ]
    assert positions == sorted(positions)


def test_description_for_fresh_database():
    text = describe_target(TARGET, "public", {})
    assert "Keine vorhandenen Tabellen: Es wird nichts gelöscht." in text
    assert "Zeile" not in text
    assert "Agenten" not in text
    assert "Host:      db.example.com" in text


# drop_statements


def test_reset_tables_are_the_six_tables_in_fk_order():
    assert RESET_TABLES == (
        "activities",
        "product_fits",
        "contacts",
        "discount_rules",
        "customers",
        "products",
    )
    order = {name: index for index, name in enumerate(RESET_TABLES)}
    assert order["activities"] < order["contacts"] < order["customers"]
    assert order["activities"] < order["products"]
    assert order["product_fits"] < order["products"]


def test_drop_statements_are_fixed_and_safe():
    statements = drop_statements()
    assert statements == [f"DROP TABLE IF EXISTS {name}" for name in RESET_TABLES]
    assert len(statements) == 6
    for statement in statements:
        assert statement.startswith("DROP TABLE IF EXISTS ")
        assert statement.removeprefix("DROP TABLE IF EXISTS ") in RESET_TABLES
        assert "CASCADE" not in statement.upper()
        assert "SCHEMA" not in statement.upper()
        assert ";" not in statement
        assert statement == statement.strip()


def test_drop_statements_returns_a_new_list_each_time():
    first = drop_statements()
    first.clear()
    assert len(drop_statements()) == 6


# Vergleich mit dem Parser von libpq
#
# psycopg.conninfo.conninfo_to_dict zerlegt eine URL wie libpq. Für jede URL gilt: Entweder
# lehnt parse_target sie ab, oder Host (klein geschrieben) und Datenbankname sind dieselben wie
# bei libpq. Kann libpq die URL selbst nicht zerlegen, ist der Fall in Ordnung. Wo urlsplit und
# libpq verschieden zerlegen würden, lehnt parse_target die URL ab, statt umzurechnen.

LIBPQ_URLS = [case[0] for case in VALID_CASES] + [
    # knifflige URLs
    "postgresql://u:p@lo%63alhost/db",
    "postgresql://u:p@db.example.com./db",
    "postgresql://u:p@DB.Example.COM/db",
    "postgresql://u:p@[2001:DB8::1]:5432/db",
    "postgresql://u:p%40ss@db.example.com/db",
    "postgresql://u:p@ss@db.example.com/db",
    "postgresql://u:p@db.example.com/db?%68ost=evil.example",
    "postgresql://u:p@db.example.com/my%20d%62",
    "POSTGRESQL://db.example.com/db",
    " postgresql://db.example.com/db",
    "postgresql://db.example.com/db\n",
    "postgresql://db.exam\nple.com/db",
    "postgresql://db.example.com?x=y@evil.example/db",
    "postgresql://h.example?x=y@db.example.com/db",
    "postgresql://u:p@db.example.com/%ff",
    "postgresql://u:p@h1.example,h2.example/db",
    "postgresql:///db",
    "postgresql://u:p@db.example.com/db?host=evil.example",
    "postgresql://u:p@db.example.com/db?HOST=evil.example",
    "postgresql://u:p@db.example.com/db?hostaddr=192.0.2.1",
    "postgresql://u:p@db.example.com/db?service=x",
    "postgresql://u:p@db.example.com/db?dbname=other",
    "postgresql://u:p@db.example.com:99999/db",
    "postgresql://u:pa/ss@db.example.com/db",
    "postgresql://u:12/ss@db.example.com/db",
    "postgresql://@db.example.com/db",
    "postgresql://db.example.com:/db",
    "postgresql://db.example.com/db/",
]


@pytest.fixture
def libpq():
    """Die Module psycopg und psycopg.conninfo (psycopg steht in requirements-dev.txt)."""
    return psycopg, conninfo


def _compare_with_libpq(url, psycopg, conninfo) -> bool:
    """Vergleicht parse_target mit libpq. Gibt True zurück, wenn wirklich verglichen wurde.

    False heißt: parse_target lehnt die URL ab, oder libpq kann sie nicht zerlegen
    (psycopg.ProgrammingError). Bei einem Unterschied von Host oder Datenbankname schlägt eine
    Zusicherung an.
    """
    try:
        mine = parse_target(url, {})
    except SeedGuardError:
        return False
    try:
        parsed = conninfo.conninfo_to_dict(url)
    except psycopg.ProgrammingError:
        return False
    assert mine.host == parsed.get("host", "").lower(), url
    assert mine.dbname == parsed.get("dbname"), url
    return True


@pytest.mark.parametrize("url", LIBPQ_URLS, ids=[f"url-{n:02d}" for n in range(len(LIBPQ_URLS))])
def test_parse_target_agrees_with_libpq(libpq, url):
    _compare_with_libpq(url, *libpq)


def test_libpq_comparison_really_compares(libpq):
    # Sonst könnte der Test grün sein, weil jede URL abgelehnt wurde oder libpq keine zerlegt hat
    compared = sum(_compare_with_libpq(url, *libpq) for url in LIBPQ_URLS)
    assert compared >= len(VALID_CASES), (
        f"Nur {compared} von {len(LIBPQ_URLS)} URLs wurden verglichen, "
        f"erwartet mindestens {len(VALID_CASES)}."
    )


@pytest.mark.parametrize(
    "fake_result",
    [
        {"host": "anderer.example", "dbname": "postgres"},
        {"host": "db.example.com", "dbname": "andere_datenbank"},
        {"dbname": "postgres"},
    ],
    ids=["anderer-host", "andere-datenbank", "host-fehlt"],
)
def test_libpq_comparison_detects_a_difference(libpq, monkeypatch, fake_result):
    psycopg, conninfo = libpq
    url = VALID_CASES[0][0]
    assert _compare_with_libpq(url, psycopg, conninfo) is True
    monkeypatch.setattr(conninfo, "conninfo_to_dict", lambda *args, **kwargs: fake_result)
    with pytest.raises(AssertionError):
        _compare_with_libpq(url, psycopg, conninfo)


# Zeichensatz der Meldungen

ALLOWED = re.compile(r"[\x20-\x7EäöüÄÖÜß\n]*")


def _all_messages() -> list[str]:
    messages = []
    for _, url, environ, _ in PARSE_ERRORS:
        with pytest.raises(SeedGuardError) as excinfo:
            parse_target(url, environ)
        messages.append(str(excinfo.value))
    with pytest.raises(SeedGuardError) as excinfo:
        verify_effective_host(TARGET, "anderer.example")
    messages.append(str(excinfo.value))
    with pytest.raises(SeedGuardError) as excinfo:
        verify_effective_host(TARGET, None)
    messages.append(str(excinfo.value))
    with pytest.raises(SeedGuardError) as excinfo:
        check_reset_confirmation(TARGET, None, EXISTING)
    messages.append(str(excinfo.value))
    messages.append(describe_target(TARGET, "public", EXISTING))
    messages.append(describe_target(TARGET, "public", {}))
    return messages


def test_messages_use_only_german_and_ascii_characters():
    for message in _all_messages():
        assert ALLOWED.fullmatch(message), message
        message.encode("cp1252")
