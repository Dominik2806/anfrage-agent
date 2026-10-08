"""Paritätstest: hoffmann_data/dburl.py gegen db/seed/guard.py (F07).

dburl ist eine gekürzte Kopie der URL-Regeln des Seed-Skripts (der Datenservice darf db.seed nicht
importieren). Dieser Test hält beide zusammen: Was das Seed-Skript ablehnt, lehnt dburl auch ab, und die
gültigen URLs nehmen beide an. Ablehnungen nur durch dburl sind erlaubt, müssen aber hier mit Grund
benannt sein (STRICTER_THAN_GUARD).
"""

import sys

import pytest
from mcp_testkit import DBURL_INVALID, DBURL_VALID, ROOT

if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

# Fälle, die dburl ablehnt, das Seed-Skript aber annimmt: Fall-ID -> Grund. Anfangs leer.
STRICTER_THAN_GUARD: dict[str, str] = {}


def _guard_rejects(url: str | None, environ: dict[str, str]) -> bool:
    from db.seed.guard import SeedGuardError, parse_target, require_secure_transport

    try:
        target = parse_target(url, environ)
        require_secure_transport(url or "", target)
    except SeedGuardError:
        return True
    return False


def _dburl_rejects(url: str | None, environ: dict[str, str]) -> bool:
    from hoffmann_data.dburl import check_database_url

    return check_database_url(url, environ) is not None


@pytest.mark.parametrize(("case", "url"), DBURL_VALID, ids=[c for c, _ in DBURL_VALID])
def test_both_accept_the_valid_urls(case: str, url: str) -> None:
    assert not _guard_rejects(url, {}), "Tabelle falsch: das Seed-Skript lehnt eine gültige URL ab"
    assert not _dburl_rejects(url, {})


@pytest.mark.parametrize(
    ("case", "url", "environ"), DBURL_INVALID, ids=[c for c, _, _ in DBURL_INVALID]
)
def test_what_the_seed_guard_rejects_dburl_rejects_too(
    case: str, url: str | None, environ: dict[str, str]
) -> None:
    if _guard_rejects(url, environ):
        assert _dburl_rejects(url, environ), "dburl ist lockerer als das Seed-Skript"
    else:
        assert case in STRICTER_THAN_GUARD, (
            "Das Seed-Skript nimmt diese URL an: Tabelle prüfen oder Fall mit Grund in "
            "STRICTER_THAN_GUARD eintragen"
        )
        assert _dburl_rejects(url, environ)


def test_stricter_cases_are_known_cases() -> None:
    known = {case for case, _, _ in DBURL_INVALID}
    assert set(STRICTER_THAN_GUARD) <= known
    assert all(reason.strip() for reason in STRICTER_THAN_GUARD.values())
