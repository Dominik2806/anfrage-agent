"""Tests der Prüfung der Datenbank-URL des Datenservice (F07, hoffmann_data/dburl.py).

Reine Funktion ohne Datenbank und ohne Umgebung: check_database_url(url, environ) gibt einen festen
Fehlertext oder None zurück und wirft nie. Der Text enthält nie einen Teil der URL (Benutzer, Passwort,
Host, Datenbankname). Dieselben Regeln wie db/seed/guard.py; der Paritätstest steht in
test_dburl_parity.py.
"""

import pytest
from mcp_testkit import DB_SECRETS, DBURL_INVALID, DBURL_VALID


def _check(url: str | None, environ: dict[str, str] | None = None) -> str | None:
    from hoffmann_data.dburl import check_database_url

    return check_database_url(url, environ or {})


@pytest.mark.parametrize(("case", "url"), DBURL_VALID, ids=[c for c, _ in DBURL_VALID])
def test_valid_url_is_accepted(case: str, url: str) -> None:
    assert _check(url) is None


@pytest.mark.parametrize(
    ("case", "url", "environ"), DBURL_INVALID, ids=[c for c, _, _ in DBURL_INVALID]
)
def test_invalid_url_is_rejected_with_a_message(
    case: str, url: str | None, environ: dict[str, str]
) -> None:
    message = _check(url, environ)
    assert isinstance(message, str) and message.strip()


@pytest.mark.parametrize(
    ("case", "url", "environ"), DBURL_INVALID, ids=[c for c, _, _ in DBURL_INVALID]
)
def test_message_never_contains_a_part_of_the_url(
    case: str, url: str | None, environ: dict[str, str]
) -> None:
    message = _check(url, environ) or ""
    for secret in DB_SECRETS:
        assert secret not in message, secret
    for value in environ.values():
        assert value not in message


def test_message_for_a_remote_host_without_tls_names_the_sslmode_values() -> None:
    url = "postgresql://geheimuser:geheimpasswort@geheimhost.example/geheimdb"
    message = _check(url) or ""
    assert "sslmode" in message
    assert "require" in message and "verify-full" in message


def test_local_hosts_need_no_tls() -> None:
    for host in ("localhost", "127.0.0.1", "[::1]"):
        assert _check(f"postgresql://u:p@{host}:5432/db") is None


def test_url_is_not_trimmed_or_changed() -> None:
    """Wie beim Token: Was nicht genau so gültig ist, wie es dasteht, wird abgelehnt statt bereinigt."""
    assert _check(" postgresql://u:p@localhost/db") is not None
    assert _check("postgresql://u:p@localhost/db ") is not None


def test_function_never_raises_on_odd_input() -> None:
    odd = ["\x00", "\ud800", "x" * 100_000, "postgresql://" + "@" * 1000, "postgresql://[::1"]
    for url in odd:
        assert isinstance(_check(url), str), repr(url)[:40]


def test_module_exposes_only_check_database_url() -> None:
    """Das Modul bleibt so klein wie möglich: eine öffentliche Funktion, keine Klassen."""
    import inspect

    from hoffmann_data import dburl

    public = {
        name
        for name, obj in vars(dburl).items()
        if not name.startswith("_")
        and (inspect.isfunction(obj) or inspect.isclass(obj))
        and getattr(obj, "__module__", None) == dburl.__name__
    }
    assert public == {"check_database_url"}
