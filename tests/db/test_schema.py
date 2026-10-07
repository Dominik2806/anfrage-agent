"""Tests der Constraints in db/schema.sql (F05, Quelle: docs/DATENMODELL.md).

Je Constraint ein positiver und ein negativer Fall. Negative Fälle prüfen Fehlerklasse und
Constraint-Namen (bei UNIQUE und FK die PostgreSQL-Standardnamen), damit ein Test nicht aus
dem falschen Grund besteht. Sie laufen in einem Savepoint, damit ein Test beides prüfen kann.
Jeder Test läuft in einer Transaktion, die die Fixture conn am Ende zurückrollt.
"""

import uuid
from contextlib import contextmanager
from decimal import Decimal

import pytest
from psycopg import errors, sql
from psycopg.types.json import Jsonb

TABLES = [
    "products",
    "product_fits",
    "customers",
    "contacts",
    "activities",
    "discount_rules",
]

# Alle NOT-NULL-Spalten außer id (Identity). Der Meta-Test unten vergleicht mit dem Schema.
NOT_NULL_COLUMNS = {
    "products": [
        "article_number",
        "name",
        "category",
        "description",
        "technical_data",
        "list_price",
        "price_unit",
        "lead_time_days",
        "is_active",
    ],
    "product_fits": ["product_id", "fits_product_id"],
    "customers": ["company_name", "domain", "industry", "country", "status", "created_at"],
    "contacts": ["customer_id", "first_name", "last_name", "email", "language", "created_at"],
    "activities": ["customer_id", "type", "occurred_at", "subject", "summary", "created_by"],
    "discount_rules": ["min_quantity", "max_discount_percent", "description"],
}
NOT_NULL_PARAMS = [(t, c) for t, cols in NOT_NULL_COLUMNS.items() for c in cols]


@contextmanager
def rejected(conn, error, constraint):
    """Erwartet Fehlerklasse und Constraint-Namen; der Savepoint hält die Transaktion nutzbar.

    constraint=None nur bei Fehlern ohne Constraint (GENERATED ALWAYS).
    """
    with pytest.raises(error) as excinfo, conn.transaction():
        yield
    if constraint is not None:
        assert excinfo.value.diag.constraint_name == constraint


@contextmanager
def rejected_not_null(conn, column):
    """Erwartet NOT NULL und prüft die betroffene Spalte."""
    with pytest.raises(errors.NotNullViolation) as excinfo, conn.transaction():
        yield
    assert excinfo.value.diag.column_name == column


def make_row(make, table, **kw):
    """Gültige Zeile in beliebiger Tabelle samt nötiger Elternzeilen."""
    if table == "products":
        return make.product(**kw)
    if table == "product_fits":
        return make.fit(make.product(), make.product(), **kw)
    if table == "customers":
        return make.customer(**kw)
    if table == "contacts":
        return make.contact(make.customer(), **kw)
    if table == "activities":
        return make.activity(make.customer(), **kw)
    if table == "discount_rules":
        return make.rule(**kw)
    raise AssertionError(f"unbekannte Tabelle {table}")


def make_row_with_null(make, table, column):
    """Wie make_row, aber mit SQL NULL in genau einer Spalte."""
    if table == "product_fits":
        return make.fit(
            None if column == "product_id" else make.product(),
            None if column == "fits_product_id" else make.product(),
        )
    if table in ("contacts", "activities") and column == "customer_id":
        return (make.contact if table == "contacts" else make.activity)(None)
    return make_row(make, table, **{column: None})


# --- Prüfung der Datenbank-URL (ohne Datenbank) -----------------------------


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://postgres:test@localhost:5432/postgres",
        "postgres://u:p@127.0.0.1/db",
        "postgresql://u:p@[::1]:5432/db",
        "postgresql://u:p@LOCALHOST/db",
        "postgresql://u:p@localhost:5432/db",
    ],
)
def test_url_check_accepts_local_hosts(check_url, monkeypatch, url):
    monkeypatch.delenv("PGHOSTADDR", raising=False)
    monkeypatch.delenv("PGSERVICE", raising=False)
    check_url(url)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://postgres:geheim@db.abcdefgh.supabase.co:5432/postgres",
        "postgresql://u:p@aws-0-eu-central-1.pooler.supabase.com:6543/postgres",
        "postgresql://u:p@localhost.evil.example/db",
        "postgresql://u:p@localhost,db.supabase.co/db",
        "postgresql://u:p@localhost/db?host=db.supabase.co",
        "postgresql://u:p@localhost/db?hostaddr=10.0.0.5",
        "postgresql://u:p@localhost/db?service=prod",
        "postgresql://u:p@localhost:5432,db.supabase.co:5432/db",
        "postgresql://u:p@[::1]:5432,db.supabase.co/db",
        "postgresql://u:p@localhost:6543/db",
        "postgresql://u:p@localhost:abc/db",
        "postgresql://u:p@localhost/db?host=",
        "postgresql:///db",
        "postgresql://u:p@/db",
        "host=localhost dbname=db",
        "mysql://u:p@localhost/db",
        "",
    ],
)
def test_url_check_rejects_other_hosts_and_forms(check_url, url):
    with pytest.raises(ValueError):
        check_url(url)


@pytest.mark.parametrize("name", ["PGHOSTADDR", "PGSERVICE"])
def test_url_check_rejects_overriding_environment_variable(check_url, monkeypatch, name):
    monkeypatch.delenv("PGHOSTADDR", raising=False)
    monkeypatch.delenv("PGSERVICE", raising=False)
    url = "postgresql://u:p@localhost:5432/db"
    check_url(url)  # ohne die Variable ist die URL gültig: nur sie ist der Grund
    monkeypatch.setenv(name, "10.0.0.5")
    with pytest.raises(ValueError, match=name):
        check_url(url)


def test_url_check_message_names_host_but_not_password(check_url):
    url = "postgresql://postgres:geheimes-passwort@db.abc.supabase.co/postgres"
    with pytest.raises(ValueError) as excinfo:
        check_url(url)
    assert "db.abc.supabase.co" in str(excinfo.value)
    assert "geheimes-passwort" not in str(excinfo.value)


# --- products ---------------------------------------------------------------


@pytest.mark.parametrize("number", ["AB-1234", "AB-1234-X1", "ZZ-0000-A", "AB-1234-9ZZZ"])
def test_article_number_format_ok(make, number):
    make.product(article_number=number)


@pytest.mark.parametrize(
    "number",
    ["ab-1234", "A-1234", "AB-123", "AB-12345", "AB-1234-", "AB-1234-toolong", "AB-1234-ABCDE"],
)
def test_article_number_format_invalid(conn, make, number):
    with rejected(conn, errors.CheckViolation, "products_article_number_format"):
        make.product(article_number=number)


def test_article_number_duplicate(conn, make):
    make.product(article_number="AB-1111")
    with rejected(conn, errors.UniqueViolation, "products_article_number_key"):
        make.product(article_number="AB-1111")


@pytest.mark.parametrize("value", ["conveyor", "housing", "spare_part", "service_contract"])
def test_category_ok(make, value):
    make.product(category=value)


def test_category_invalid(conn, make):
    with rejected(conn, errors.CheckViolation, "products_category_check"):
        make.product(category="tool")


@pytest.mark.parametrize("value", ["piece", "meter", "year"])
def test_price_unit_ok(make, value):
    make.product(price_unit=value)


def test_price_unit_invalid(conn, make):
    with rejected(conn, errors.CheckViolation, "products_price_unit_check"):
        make.product(price_unit="kg")


def test_list_price_zero_ok(make):
    make.product(list_price=0)


def test_list_price_negative(conn, make):
    with rejected(conn, errors.CheckViolation, "products_list_price_check"):
        make.product(list_price=Decimal("-0.01"))


def test_lead_time_days_zero_ok(make):
    make.product(lead_time_days=0)


def test_lead_time_days_negative(conn, make):
    with rejected(conn, errors.CheckViolation, "products_lead_time_days_check"):
        make.product(lead_time_days=-1)


def test_technical_data_object_ok(make):
    make.product(technical_data={})
    make.product(technical_data={"belt_width_mm": 400})


@pytest.mark.parametrize(
    "value",
    [[], [1, 2], "text", 5, pytest.param(Jsonb(None), id="json-null")],
)
def test_technical_data_not_object(conn, make, value):
    # SQL NULL ist ein anderer Fall und steht bei den NOT-NULL-Tests
    with rejected(conn, errors.CheckViolation, "products_technical_data_object"):
        make.product(technical_data=value)


# --- product_fits -----------------------------------------------------------


def test_product_fits_ok(make):
    part = make.product(category="spare_part")
    plant = make.product()
    make.fit(part, plant)


def test_product_fits_with_itself(conn, make):
    p = make.product()
    with rejected(conn, errors.CheckViolation, "product_fits_not_self"):
        make.fit(p, p)


def test_product_fits_duplicate(conn, make):
    part = make.product(category="spare_part")
    plant = make.product()
    make.fit(part, plant)
    with rejected(conn, errors.UniqueViolation, "product_fits_pair_key"):
        make.fit(part, plant)


def test_product_fits_reverse_pair_is_distinct(make):
    a = make.product()
    b = make.product()
    make.fit(a, b)
    make.fit(b, a)


def test_product_fits_unknown_fits_product(conn, make):
    p = make.product()
    with rejected(conn, errors.ForeignKeyViolation, "product_fits_fits_product_id_fkey"):
        make.fit(p, 999_999_999)


def test_product_fits_unknown_product(conn, make):
    p = make.product()
    with rejected(conn, errors.ForeignKeyViolation, "product_fits_product_id_fkey"):
        make.fit(999_999_999, p)


def test_product_as_part_in_fits_not_deletable(conn, make):
    part = make.product(category="spare_part")
    plant = make.product()
    make.fit(part, plant)
    with rejected(conn, errors.ForeignKeyViolation, "product_fits_product_id_fkey"):
        conn.execute("DELETE FROM products WHERE id = %s", (part,))


def test_product_as_plant_in_fits_not_deletable(conn, make):
    part = make.product(category="spare_part")
    plant = make.product()
    make.fit(part, plant)
    with rejected(conn, errors.ForeignKeyViolation, "product_fits_fits_product_id_fkey"):
        conn.execute("DELETE FROM products WHERE id = %s", (plant,))


# --- customers --------------------------------------------------------------


@pytest.mark.parametrize("domain", ["firma.example", "a.b.example", "my-firma2.example"])
def test_domain_ok(make, domain):
    make.customer(domain=domain)


@pytest.mark.parametrize(
    "domain",
    [
        "firma.de",
        "Firma.example",
        "firma.EXAMPLE",
        "firma.example.com",
        ".example",
        "fi rma.example",
    ],
)
def test_domain_invalid(conn, make, domain):
    with rejected(conn, errors.CheckViolation, "customers_domain_format"):
        make.customer(domain=domain)


def test_domain_duplicate(conn, make):
    make.customer(domain="gleich.example")
    with rejected(conn, errors.UniqueViolation, "customers_domain_key"):
        make.customer(domain="gleich.example")


def test_company_name_duplicate_other_case(conn, make):
    make.customer(company_name="Mueller GmbH", domain="mueller1.example")
    with rejected(conn, errors.UniqueViolation, "customers_company_name_lower_key"):
        make.customer(company_name="MUELLER GMBH", domain="mueller2.example")


def test_company_name_duplicate_other_case_with_umlaut(conn, make):
    # lower() mit Umlauten hängt von der Locale der Datenbank ab (im Postgres-Image: UTF-8)
    make.customer(company_name="Müller GmbH", domain="mueller1.example")
    with rejected(conn, errors.UniqueViolation, "customers_company_name_lower_key"):
        make.customer(company_name="MÜLLER GMBH", domain="mueller2.example")


def test_company_name_different_names_ok(make):
    make.customer(company_name="Müller GmbH")
    make.customer(company_name="Müller AG")


@pytest.mark.parametrize(
    "value", ["automotive", "food", "packaging", "metalworking", "plastics", "logistics", "other"]
)
def test_industry_ok(make, value):
    make.customer(industry=value)


def test_industry_invalid(conn, make):
    with rejected(conn, errors.CheckViolation, "customers_industry_check"):
        make.customer(industry="mining")


@pytest.mark.parametrize("value", ["de", "at", "ch"])
def test_country_ok(make, value):
    make.customer(country=value)


@pytest.mark.parametrize("value", ["fr", "DE"])
def test_country_invalid(conn, make, value):
    with rejected(conn, errors.CheckViolation, "customers_country_check"):
        make.customer(country=value)


@pytest.mark.parametrize("value", ["existing", "lead", "inactive"])
def test_status_ok(make, value):
    make.customer(status=value)


def test_status_invalid(conn, make):
    with rejected(conn, errors.CheckViolation, "customers_status_check"):
        make.customer(status="prospect")


# --- contacts ---------------------------------------------------------------


@pytest.mark.parametrize(
    "email", ["a@firma.example", "vor.nach@sub.firma.example", "a+b@x-y.example"]
)
def test_email_ok(make, email):
    make.contact(make.customer(), email=email)


@pytest.mark.parametrize(
    "email",
    [
        "x@firma.de",
        "X@firma.example",
        "x@Firma.example",
        "x firma.example",
        "x y@firma.example",
        "@firma.example",
    ],
)
def test_email_invalid(conn, make, email):
    cid = make.customer()
    with rejected(conn, errors.CheckViolation, "contacts_email_format"):
        make.contact(cid, email=email)


def test_email_duplicate(conn, make):
    cid = make.customer()
    make.contact(cid, email="doppelt@firma.example")
    with rejected(conn, errors.UniqueViolation, "contacts_email_key"):
        make.contact(cid, email="doppelt@firma.example")


def test_email_duplicate_across_customers(conn, make):
    make.contact(make.customer(), email="gleich@firma.example")
    other = make.customer()
    with rejected(conn, errors.UniqueViolation, "contacts_email_key"):
        make.contact(other, email="gleich@firma.example")


@pytest.mark.parametrize("value", ["de", "en"])
def test_language_ok(make, value):
    make.contact(make.customer(), language=value)


def test_language_invalid(conn, make):
    cid = make.customer()
    with rejected(conn, errors.CheckViolation, "contacts_language_check"):
        make.contact(cid, language="fr")


def test_contact_unknown_customer(conn, make):
    with rejected(conn, errors.ForeignKeyViolation, "contacts_customer_id_fkey"):
        make.contact(999_999_999)


def test_customer_with_contact_not_deletable(conn, make):
    cid = make.customer()
    make.contact(cid)
    with rejected(conn, errors.ForeignKeyViolation, "contacts_customer_id_fkey"):
        conn.execute("DELETE FROM customers WHERE id = %s", (cid,))


def test_customer_without_contact_and_activity_deletable(conn, make):
    cid = make.customer()
    conn.execute("DELETE FROM customers WHERE id = %s", (cid,))
    assert conn.execute("SELECT 1 FROM customers WHERE id = %s", (cid,)).fetchone() is None


def test_contact_with_activity_not_deletable(conn, make):
    cid = make.customer()
    contact = make.contact(cid)
    make.activity(cid, contact_id=contact)
    with rejected(conn, errors.ForeignKeyViolation, "activities_contact_fk"):
        conn.execute("DELETE FROM contacts WHERE id = %s", (contact,))


def test_contact_without_activity_deletable(conn, make):
    contact = make.contact(make.customer())
    conn.execute("DELETE FROM contacts WHERE id = %s", (contact,))
    assert conn.execute("SELECT 1 FROM contacts WHERE id = %s", (contact,)).fetchone() is None


# --- activities -------------------------------------------------------------


@pytest.mark.parametrize("value", ["inquiry", "quote", "order", "complaint", "service", "note"])
def test_activity_type_ok(make, value):
    make.activity(make.customer(), type=value)


def test_activity_type_invalid(conn, make):
    cid = make.customer()
    with rejected(conn, errors.CheckViolation, "activities_type_check"):
        make.activity(cid, type="call")


@pytest.mark.parametrize("value", ["seed", "agent", "staff"])
def test_created_by_ok(make, value):
    make.activity(make.customer(), created_by=value)


def test_created_by_invalid(conn, make):
    cid = make.customer()
    with rejected(conn, errors.CheckViolation, "activities_created_by_check"):
        make.activity(cid, created_by="bot")


def test_activity_contact_of_same_customer_ok(make):
    cid = make.customer()
    make.activity(cid, contact_id=make.contact(cid))


def test_activity_without_contact_ok(make):
    make.activity(make.customer(), contact_id=None)


def test_activity_contact_of_other_customer(conn, make):
    own = make.customer()
    other = make.customer()
    foreign_contact = make.contact(other)
    with rejected(conn, errors.ForeignKeyViolation, "activities_contact_fk"):
        make.activity(own, contact_id=foreign_contact)


def test_activity_unknown_product(conn, make):
    cid = make.customer()
    with rejected(conn, errors.ForeignKeyViolation, "activities_product_id_fkey"):
        make.activity(cid, product_id=999_999_999)


def test_customer_with_activity_not_deletable(conn, make):
    cid = make.customer()
    make.activity(cid)
    with rejected(conn, errors.ForeignKeyViolation, "activities_customer_id_fkey"):
        conn.execute("DELETE FROM customers WHERE id = %s", (cid,))


def test_product_in_activity_not_deletable(conn, make):
    cid = make.customer()
    pid = make.product()
    make.activity(cid, product_id=pid)
    with rejected(conn, errors.ForeignKeyViolation, "activities_product_id_fkey"):
        conn.execute("DELETE FROM products WHERE id = %s", (pid,))


def test_product_without_activity_deletable(conn, make):
    pid = make.product()
    conn.execute("DELETE FROM products WHERE id = %s", (pid,))
    assert conn.execute("SELECT 1 FROM products WHERE id = %s", (pid,)).fetchone() is None


def test_amount_zero_ok(make):
    make.activity(make.customer(), type="order", amount_eur=0)


def test_amount_negative(conn, make):
    cid = make.customer()
    with rejected(conn, errors.CheckViolation, "activities_amount_eur_check"):
        make.activity(cid, type="order", amount_eur=Decimal(-1))


@pytest.mark.parametrize("value", ["quote", "order"])
def test_amount_on_quote_and_order_ok(make, value):
    make.activity(make.customer(), type=value, amount_eur=Decimal("1500.50"))


@pytest.mark.parametrize("value", ["complaint", "inquiry", "service", "note"])
def test_amount_on_other_types(conn, make, value):
    cid = make.customer()
    with rejected(conn, errors.CheckViolation, "activities_amount_type_check"):
        make.activity(cid, type=value, amount_eur=Decimal(10))


def test_complaint_without_amount_ok(make):
    make.activity(make.customer(), type="complaint", amount_eur=None)


# --- discount_rules ---------------------------------------------------------


@pytest.mark.parametrize("value", [0, Decimal("5.5"), Decimal("99.99"), 100])
def test_max_discount_ok(make, value):
    make.rule(max_discount_percent=value)


def test_max_discount_negative(conn, make):
    with rejected(conn, errors.CheckViolation, "discount_rules_max_discount_check"):
        make.rule(max_discount_percent=-1)


@pytest.mark.parametrize("value", [Decimal("100.01"), 150])
def test_max_discount_over_100(conn, make, value):
    with rejected(conn, errors.CheckViolation, "discount_rules_max_discount_check"):
        make.rule(max_discount_percent=value)


def test_rule_duplicate_with_values(conn, make):
    make.rule(customer_status="existing", product_category="conveyor", min_quantity=3)
    with rejected(conn, errors.UniqueViolation, "discount_rules_scope_key"):
        make.rule(customer_status="existing", product_category="conveyor", min_quantity=3)


def test_rule_duplicate_with_nulls(conn, make):
    make.rule(customer_status=None, product_category=None, min_quantity=1)
    with rejected(conn, errors.UniqueViolation, "discount_rules_scope_key"):
        make.rule(customer_status=None, product_category=None, min_quantity=1)


def test_rule_duplicate_with_one_null(conn, make):
    make.rule(customer_status=None, product_category="housing", min_quantity=2)
    with rejected(conn, errors.UniqueViolation, "discount_rules_scope_key"):
        make.rule(customer_status=None, product_category="housing", min_quantity=2)


def test_rule_null_vs_value_is_distinct(make):
    make.rule(customer_status=None, product_category=None)
    make.rule(customer_status="lead", product_category=None)
    make.rule(customer_status=None, product_category="housing")
    make.rule(customer_status=None, product_category=None, min_quantity=2)


@pytest.mark.parametrize("value", ["existing", "lead", None])
def test_rule_customer_status_ok(make, value):
    make.rule(customer_status=value)


def test_rule_customer_status_inactive_invalid(conn, make):
    # 'inactive' ist für customers erlaubt, für Rabattregeln nicht
    with rejected(conn, errors.CheckViolation, "discount_rules_customer_status_check"):
        make.rule(customer_status="inactive")


@pytest.mark.parametrize("value", ["conveyor", "housing", "spare_part", "service_contract", None])
def test_rule_product_category_ok(make, value):
    make.rule(product_category=value)


def test_rule_product_category_invalid(conn, make):
    with rejected(conn, errors.CheckViolation, "discount_rules_product_category_check"):
        make.rule(product_category="tool")


def test_rule_min_quantity_one_ok(make):
    make.rule(min_quantity=1)


def test_rule_min_quantity_zero(conn, make):
    with rejected(conn, errors.CheckViolation, "discount_rules_min_quantity_check"):
        make.rule(min_quantity=0)


# --- NOT NULL, Standardwerte, Identity ---------------------------------------


@pytest.mark.parametrize(("table", "column"), NOT_NULL_PARAMS)
def test_not_null(conn, make, table, column):
    with rejected_not_null(conn, column):
        make_row_with_null(make, table, column)


def test_not_null_tests_cover_all_not_null_columns(conn, db_schema):
    rows = conn.execute(
        """
        SELECT table_name, column_name
        FROM information_schema.columns
        WHERE table_schema = %s AND is_nullable = 'NO' AND column_name <> 'id'
        """,
        (db_schema,),
    ).fetchall()
    assert sorted(rows) == sorted(NOT_NULL_PARAMS)


def test_products_defaults(conn, make):
    pid = make.product()
    row = conn.execute(
        "SELECT technical_data, is_active FROM products WHERE id = %s", (pid,)
    ).fetchone()
    assert row == ({}, True)


def test_discount_rules_default_min_quantity(conn, make):
    rid = make.rule()
    row = conn.execute("SELECT min_quantity FROM discount_rules WHERE id = %s", (rid,)).fetchone()
    assert row == (1,)


@pytest.mark.parametrize("table", ["customers", "contacts"])
def test_created_at_defaults_to_now(conn, make, table):
    rid = make_row(make, table)
    # now() ist innerhalb einer Transaktion konstant
    row = conn.execute(
        sql.SQL("SELECT created_at = now() FROM {} WHERE id = %s").format(sql.Identifier(table)),
        (rid,),
    ).fetchone()
    assert row == (True,)


@pytest.mark.parametrize("table", TABLES)
def test_explicit_id_rejected(conn, make, table):
    with rejected(conn, errors.GeneratedAlways, None):
        make_row(make, table, id=1)


# --- Row Level Security -----------------------------------------------------


@pytest.mark.parametrize("table", TABLES)
def test_rls_enabled(conn, db_schema, table):
    row = conn.execute(
        """
        SELECT c.relrowsecurity
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relname = %s AND c.relkind = 'r'
        """,
        (db_schema, table),
    ).fetchone()
    assert row is not None, f"Tabelle {table} fehlt im Testschema"
    assert row[0] is True


def test_no_policies(conn, db_schema):
    rows = conn.execute(
        "SELECT tablename, policyname FROM pg_policies WHERE schemaname = %s",
        (db_schema,),
    ).fetchall()
    assert rows == []


def test_schema_has_exactly_the_six_tables(conn, db_schema):
    rows = conn.execute(
        """
        SELECT c.relname
        FROM pg_class c
        JOIN pg_namespace n ON n.oid = c.relnamespace
        WHERE n.nspname = %s AND c.relkind = 'r'
        """,
        (db_schema,),
    ).fetchall()
    assert sorted(r[0] for r in rows) == sorted(TABLES)


def test_rls_hides_all_rows_from_role_without_bypass(conn, make, db_schema):
    # Rollen gelten für den ganzen Server: Zufallsname, wird mit der Transaktion zurückgerollt
    role_name = f"rls_probe_{uuid.uuid4().hex[:12]}"
    role = sql.Identifier(role_name)
    schema = sql.Identifier(db_schema)
    conn.execute(sql.SQL("CREATE ROLE {} NOLOGIN").format(role))
    flags = conn.execute(
        "SELECT rolbypassrls, rolsuper FROM pg_roles WHERE rolname = %s", (role_name,)
    ).fetchone()
    assert flags == (False, False)
    conn.execute(sql.SQL("GRANT USAGE ON SCHEMA {} TO {}").format(schema, role))
    conn.execute(sql.SQL("GRANT SELECT ON ALL TABLES IN SCHEMA {} TO {}").format(schema, role))
    for table in TABLES:
        make_row(make, table)

    def counts():
        return {
            t: conn.execute(
                sql.SQL("SELECT count(*) FROM {}").format(sql.Identifier(t))
            ).fetchone()[0]
            for t in TABLES
        }

    conn.execute(sql.SQL("SET LOCAL ROLE {}").format(role))
    restricted = counts()
    conn.execute("RESET ROLE")
    visible = counts()
    assert restricted == {t: 0 for t in TABLES}
    assert all(n > 0 for n in visible.values()), visible
