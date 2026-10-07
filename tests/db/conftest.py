"""Fixtures für die Schema-Tests (F05).

Die Verbindung kommt ausschließlich aus TEST_DATABASE_URL (DATABASE_URL wird bewusst nie
verwendet) und darf nur auf einen lokalen Host, Port 5432, zeigen (check_test_database_url).
Fehlt die Variable, werden lokal nur die Tests übersprungen, die die Datenbank brauchen.
In GitHub Actions ist das ein Fehler: Die Pipeline darf nie grün sein, ohne dass sie lief.
Das Schema wird einmal je Lauf in einem eigenen Schema (test_<zufall>) angelegt und am
Ende gelöscht, nie in public. Jeder Test läuft in einer Transaktion, die zurückgerollt wird.
"""

import itertools
import os
import uuid
from pathlib import Path
from urllib.parse import parse_qs, urlsplit

import psycopg
import pytest
from psycopg import sql
from psycopg.types.json import Jsonb

ENV_NAME = "TEST_DATABASE_URL"
SCHEMA_SQL = Path(__file__).resolve().parents[2] / "db" / "schema.sql"
HERE = Path(__file__).resolve().parent

ALLOWED_HOSTS = ("localhost", "127.0.0.1", "::1")
ALLOWED_PORT = 5432
# libpq stellt diese Parameter über den Host der URL
OVERRIDING_PARAMS = ("host", "hostaddr", "service")
# Diese Umgebungsvariablen überstimmen den Host der URL
OVERRIDING_ENV = ("PGHOSTADDR", "PGSERVICE")


def check_test_database_url(url):
    """Lässt nur URLs mit lokalem Host und Port 5432 zu, sonst ValueError.

    Reine Funktion ohne Datenbankzugriff. Die Meldung nennt den Host, nie das Passwort.
    PGHOSTADDR und PGSERVICE überstimmen den geprüften Host und werden deshalb abgelehnt.
    """
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port
    except ValueError:
        raise ValueError(f"{ENV_NAME} ist keine lesbare URL.") from None
    if parts.scheme not in ("postgresql", "postgres"):
        raise ValueError(
            f"{ENV_NAME} muss eine URL der Form postgresql://... sein "
            "(Angaben der Form host=... sind nicht erlaubt)."
        )
    if "," in parts.netloc:
        raise ValueError(f"{ENV_NAME} darf nur einen Host enthalten (kein Komma in der Adresse).")
    overriding = sorted(
        k for k in parse_qs(parts.query, keep_blank_values=True) if k.lower() in OVERRIDING_PARAMS
    )
    if overriding:
        raise ValueError(
            f"{ENV_NAME} darf die Parameter {', '.join(overriding)} nicht enthalten: "
            "Sie würden den geprüften Host überstimmen."
        )
    if host not in ALLOWED_HOSTS:
        raise ValueError(
            f"{ENV_NAME}: Host {host or '(keiner)'!r} ist nicht erlaubt. "
            f"Die Schema-Tests laufen nur gegen {', '.join(ALLOWED_HOSTS)}, "
            "nie gegen die echte Datenbank."
        )
    if port not in (None, ALLOWED_PORT):
        raise ValueError(
            f"{ENV_NAME}: Port {port} ist nicht erlaubt, nur {ALLOWED_PORT} (oder keine Angabe)."
        )
    env_set = [name for name in OVERRIDING_ENV if name in os.environ]
    if env_set:
        raise ValueError(
            f"Die Umgebungsvariable(n) {', '.join(env_set)} würden den geprüften Host "
            "überstimmen und dürfen für die Schema-Tests nicht gesetzt sein."
        )


def pytest_collection_modifyitems(config, items):
    own = [i for i in items if HERE in Path(i.path).resolve().parents]
    url = os.environ.get(ENV_NAME)
    if url:
        try:
            check_test_database_url(url)
        except ValueError as exc:
            pytest.exit(str(exc), returncode=2)
        return
    needs_db = [i for i in own if "db_url" in i.fixturenames]
    if not needs_db:
        return
    if os.environ.get("GITHUB_ACTIONS") == "true":
        pytest.exit(
            f"{ENV_NAME} fehlt in GitHub Actions: Die Schema-Tests dürfen dort nicht "
            "übersprungen werden.",
            returncode=2,
        )
    marker = pytest.mark.skip(
        reason=f"{ENV_NAME} ist nicht gesetzt: Tests mit Datenbank werden übersprungen "
        "(DATABASE_URL wird bewusst nie verwendet)."
    )
    for item in needs_db:
        item.add_marker(marker)


@pytest.fixture
def check_url():
    """Die Prüffunktion für die Tests, die ohne Datenbank laufen."""
    return check_test_database_url


@pytest.fixture(scope="session")
def schema_name():
    name = f"test_{uuid.uuid4().hex[:12]}"
    assert name != "public" and name.startswith("test_")
    return name


@pytest.fixture(scope="session")
def db_url():
    url = os.environ[ENV_NAME]
    check_test_database_url(url)
    return url


@pytest.fixture(scope="session")
def db_schema(db_url, schema_name):
    """Legt das Testschema an, spielt db/schema.sql ein und löscht es am Ende."""
    ident = sql.Identifier(schema_name)
    with psycopg.connect(db_url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE SCHEMA {}").format(ident))
        try:
            # search_path nur auf das Testschema: unqualifizierte Namen landen nie in public
            with psycopg.connect(
                db_url, autocommit=True, options=f"-csearch_path={schema_name}"
            ) as setup:
                setup.execute(SCHEMA_SQL.read_text(encoding="utf-8"))
            yield schema_name
        finally:
            admin.execute(sql.SQL("DROP SCHEMA {} CASCADE").format(ident))


@pytest.fixture
def conn(db_url, db_schema):
    """Eine äußere Transaktion je Test, immer zurückgerollt.

    So ist jede Transaktion in den Tests (rejected) ein Savepoint, und eine Zeile, die ein
    fehlender Constraint durchlässt, wird trotzdem verworfen.
    """
    with (
        psycopg.connect(db_url, options=f"-csearch_path={db_schema}") as c,
        c.transaction(force_rollback=True),
    ):
        yield c


_counter = itertools.count(1)


@pytest.fixture
def make(conn):
    """Fabrik für gültige Minimalzeilen; Overrides per Schlüsselwort. Gibt die id zurück.

    None wird als SQL NULL gespeichert, auch bei technical_data. JSON null ist Jsonb(None).
    """

    def _insert(table, defaults, overrides):
        row = {**defaults, **overrides}
        cols = sql.SQL(", ").join(sql.Identifier(k) for k in row)
        marks = sql.SQL(", ").join(sql.Placeholder() for _ in row)
        q = sql.SQL("INSERT INTO {} ({}) VALUES ({}) RETURNING id").format(
            sql.Identifier(table), cols, marks
        )
        values = [
            Jsonb(v) if k == "technical_data" and v is not None and not isinstance(v, Jsonb) else v
            for k, v in row.items()
        ]
        return conn.execute(q, values).fetchone()[0]

    class Make:
        def product(self, **kw):
            n = next(_counter)
            return _insert(
                "products",
                {
                    "article_number": f"AB-{n:04d}",
                    "name": "Testartikel",
                    "category": "conveyor",
                    "description": "Testbeschreibung",
                    "list_price": 10,
                    "price_unit": "piece",
                    "lead_time_days": 5,
                },
                kw,
            )

        def customer(self, **kw):
            n = next(_counter)
            return _insert(
                "customers",
                {
                    "company_name": f"Testfirma {n}",
                    "domain": f"firma{n}.example",
                    "industry": "other",
                    "country": "de",
                    "status": "existing",
                },
                kw,
            )

        def contact(self, customer_id, **kw):
            n = next(_counter)
            return _insert(
                "contacts",
                {
                    "customer_id": customer_id,
                    "first_name": "Erika",
                    "last_name": "Muster",
                    "email": f"kontakt{n}@firma.example",
                    "language": "de",
                },
                kw,
            )

        def activity(self, customer_id, **kw):
            return _insert(
                "activities",
                {
                    "customer_id": customer_id,
                    "type": "inquiry",
                    "occurred_at": "2026-01-01T10:00:00+00",
                    "subject": "Betreff",
                    "summary": "Zusammenfassung",
                    "created_by": "seed",
                },
                kw,
            )

        def fit(self, product_id, fits_product_id, **kw):
            return _insert(
                "product_fits",
                {"product_id": product_id, "fits_product_id": fits_product_id},
                kw,
            )

        def rule(self, **kw):
            return _insert(
                "discount_rules",
                {"max_discount_percent": 5, "description": "Testregel"},
                kw,
            )

    return Make()
