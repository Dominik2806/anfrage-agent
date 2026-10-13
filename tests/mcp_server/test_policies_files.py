"""Tests des Richtlinien-Laders und des Start-Checks (F09, ADR 0006): hoffmann_data/policies.py und main().

Schnittstelle, die diese Tests festlegen (policies.py):
- POLICY_DIR: Modulkonstante, der Ordner data/richtlinien der Projektwurzel. Es gibt keine Umgebungsvariable. Wer
  den Ordner ersetzen will (Tests), setzt die Konstante mit monkeypatch; main() und create_app lesen sie zur
  Aufrufzeit über das Modul.
- MAX_POLICY_BYTES (64 KiB), POLICY_UNAVAILABLE (die eine feste Meldung), PolicyError (trägt genau diese Meldung).
- read_policy(directory, filename) -> str: je Aufruf lesen, höchstens MAX_POLICY_BYTES Bytes, strenges UTF-8,
  CRLF wird LF. Fehlt die Datei, ist es ein Ordner, ist sie zu groß, kein UTF-8 oder leer: PolicyError, immer mit
  derselben Meldung, ohne Pfad, Dateinamen und Ausnahmetext.
- tonalitaet_text(directory), rabatte_text(directory) (RABATTE_PREAMBLE + eskalation.md + RABATTE_SEPARATOR +
  rabatte.md), signature_section(directory, sprache) (Abschnitt "## Deutsch" bzw. "## Englisch" plus "## Regeln";
  die Datei muss alle drei Abschnitte haben, sonst PolicyError, auch wenn der andere Sprachabschnitt fehlt).
- verify_policies(directory) -> list[str]: Kurzbezeichnungen ohne Pfad, in fester Reihenfolge: richtlinie-fehlt:<name>
  (Datei fehlt oder ist nicht lesbar; <name> = tonalitaet, eskalation, rabatte, signatur) und
  richtlinie-abschnitt:signatur (einer der Abschnitte "## Deutsch", "## Englisch", "## Regeln" fehlt).
- main() ruft den Check nach den Rollenprüfungen und vor uvicorn.run auf; bei Fehlern Exit 1, auf stderr nur die
  Kurzbezeichnungen.
- Nachbesserung nach den Reviews: read_policy lehnt einen Symlink ab, dessen Ziel außerhalb des Ordners liegt (ein Symlink
  auf eine Datei im selben Ordner ist erlaubt); eine BOM am Anfang der Datei wird akzeptiert und entfernt;
  signature_section mit einer unbekannten Sprache wirft PolicyError (nicht KeyError); ein doppelter Abschnitt
  ("## Deutsch", "## Englisch" oder "## Regeln" zweimal) in signatur.md ist ein Fehler: PolicyError bzw. die
  Kurzbezeichnung richtlinie-abschnitt:signatur, Exit 1 beim Start. Die Unit-Tests dazu stehen alle in dieser Datei.

Ohne Datenbank.
"""

import os
import shutil
from pathlib import Path
from typing import Any

import pytest
from mcp_testkit import GOOD_READ_URL, ROOT

REAL_DIR = ROOT / "data" / "richtlinien"
POLICY_FILES = ("tonalitaet.md", "eskalation.md", "rabatte.md", "signatur.md")
SHORT_MISSING = [
    "richtlinie-fehlt:tonalitaet",
    "richtlinie-fehlt:eskalation",
    "richtlinie-fehlt:rabatte",
    "richtlinie-fehlt:signatur",
]


def _policies() -> Any:
    from hoffmann_data import policies

    return policies


def _real_text(name: str) -> str:
    """Der Text einer echten Richtliniendatei, wie ihn der Lader liefern muss (LF)."""
    return (REAL_DIR / name).read_text(encoding="utf-8").replace("\r\n", "\n")


@pytest.fixture
def full_dir(tmp_path: Path) -> Path:
    """Ein Ordner mit Kopien der vier echten Richtliniendateien."""
    directory = tmp_path / "richtlinien"
    directory.mkdir()
    for name in POLICY_FILES:
        shutil.copyfile(REAL_DIR / name, directory / name)
    return directory


@pytest.fixture
def empty_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "leer"
    directory.mkdir()
    return directory


def _assert_fixed_error(error: BaseException, tmp_path: Path, name: str) -> None:
    policies = _policies()
    assert isinstance(error, policies.PolicyError)
    assert str(error) == policies.POLICY_UNAVAILABLE
    for leaked in (str(tmp_path), tmp_path.name, name, name.removesuffix(".md")):
        assert leaked not in str(error), leaked


# Konstanten und Standardordner


def test_default_directory_is_the_data_folder_of_the_project() -> None:
    assert Path(_policies().POLICY_DIR).resolve() == REAL_DIR.resolve()


def test_policy_constants() -> None:
    policies = _policies()
    assert policies.MAX_POLICY_BYTES == 64 * 1024
    assert policies.POLICY_UNAVAILABLE.strip()


def test_the_real_policy_files_are_within_the_size_limit() -> None:
    for name in POLICY_FILES:
        assert (REAL_DIR / name).stat().st_size < _policies().MAX_POLICY_BYTES


# read_policy


@pytest.mark.parametrize("name", POLICY_FILES)
def test_read_policy_returns_the_text_of_the_file(full_dir: Path, name: str) -> None:
    assert _policies().read_policy(full_dir, name) == _real_text(name)


def test_read_policy_turns_crlf_into_lf(empty_dir: Path) -> None:
    (empty_dir / "tonalitaet.md").write_bytes(b"Zeile eins\r\nZeile zwei\r\n")
    assert _policies().read_policy(empty_dir, "tonalitaet.md") == "Zeile eins\nZeile zwei\n"


def test_read_policy_reads_the_file_on_every_call(empty_dir: Path) -> None:
    path = empty_dir / "tonalitaet.md"
    path.write_text("erste Fassung", encoding="utf-8")
    assert _policies().read_policy(empty_dir, "tonalitaet.md") == "erste Fassung"
    path.write_text("zweite Fassung", encoding="utf-8")
    assert _policies().read_policy(empty_dir, "tonalitaet.md") == "zweite Fassung"


def test_read_policy_accepts_a_file_of_exactly_the_maximum_size(empty_dir: Path) -> None:
    policies = _policies()
    (empty_dir / "tonalitaet.md").write_bytes(b"x" * policies.MAX_POLICY_BYTES)
    assert len(policies.read_policy(empty_dir, "tonalitaet.md")) == policies.MAX_POLICY_BYTES


def _make_missing(directory: Path) -> None:
    pass


def _make_directory(directory: Path) -> None:
    (directory / "tonalitaet.md").mkdir()


def _make_too_big(directory: Path) -> None:
    (directory / "tonalitaet.md").write_bytes(b"x" * (_policies().MAX_POLICY_BYTES + 1))


def _make_invalid_utf8(directory: Path) -> None:
    (directory / "tonalitaet.md").write_bytes(b"Text \xff\xfe kaputt")


def _make_empty(directory: Path) -> None:
    (directory / "tonalitaet.md").write_bytes(b"")


def _make_blank(directory: Path) -> None:
    (directory / "tonalitaet.md").write_text(" \n\t\n", encoding="utf-8")


@pytest.mark.parametrize(
    "prepare",
    [_make_missing, _make_directory, _make_too_big, _make_invalid_utf8, _make_empty, _make_blank],
    ids=["fehlt", "ordner", "zu-gross", "kein-utf8", "leer", "nur-leerraum"],
)
def test_every_failure_gives_the_one_fixed_message(
    empty_dir: Path, tmp_path: Path, prepare: Any
) -> None:
    prepare(empty_dir)
    with pytest.raises(_policies().PolicyError) as info:
        _policies().read_policy(empty_dir, "tonalitaet.md")
    _assert_fixed_error(info.value, tmp_path, "tonalitaet.md")


def test_a_missing_ordner_gives_the_same_fixed_message(tmp_path: Path) -> None:
    with pytest.raises(_policies().PolicyError) as info:
        _policies().read_policy(tmp_path / "gibt-es-nicht", "tonalitaet.md")
    _assert_fixed_error(info.value, tmp_path, "tonalitaet.md")


# Zusammensetzung


def test_tonalitaet_text_is_the_file(full_dir: Path) -> None:
    assert _policies().tonalitaet_text(full_dir) == _real_text("tonalitaet.md")


def test_rabatte_text_is_preamble_escalation_separator_and_discount_rules(full_dir: Path) -> None:
    policies = _policies()
    expected = (
        policies.RABATTE_PREAMBLE
        + _real_text("eskalation.md")
        + policies.RABATTE_SEPARATOR
        + _real_text("rabatte.md")
    )
    assert policies.rabatte_text(full_dir) == expected


@pytest.mark.parametrize("missing", ["eskalation.md", "rabatte.md"])
def test_rabatte_text_fails_when_one_of_its_files_is_missing(
    full_dir: Path, tmp_path: Path, missing: str
) -> None:
    (full_dir / missing).unlink()
    with pytest.raises(_policies().PolicyError) as info:
        _policies().rabatte_text(full_dir)
    _assert_fixed_error(info.value, tmp_path, missing)


def test_german_signature_has_the_german_part_and_the_rules(full_dir: Path) -> None:
    text = _policies().signature_section(full_dir, "de")
    assert "Freundliche Grüße" in text
    assert "[Name der freigebenden Person]" in text
    assert "Ohne Telefonnummer" in text  # Abschnitt "Regeln"
    assert "Kind regards" not in text


def test_english_signature_has_the_english_part_and_the_rules(full_dir: Path) -> None:
    text = _policies().signature_section(full_dir, "en")
    assert "Kind regards" in text
    assert "[Name of the approving person]" in text
    assert "Ohne Telefonnummer" in text
    assert "Freundliche Grüße" not in text


@pytest.mark.parametrize("heading", ["## Deutsch", "## Englisch", "## Regeln"])
def test_a_signature_without_a_section_is_unavailable(
    full_dir: Path, tmp_path: Path, heading: str
) -> None:
    path = full_dir / "signatur.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(heading, "## Anderes"), encoding="utf-8"
    )
    with pytest.raises(_policies().PolicyError) as info:
        _policies().signature_section(full_dir, "de")
    _assert_fixed_error(info.value, tmp_path, "signatur.md")


@pytest.mark.parametrize(
    "sprache", ["fr", "DE", "", "de "], ids=["fr", "gross", "leer", "leerzeichen"]
)
def test_signature_section_rejects_an_unknown_language(
    full_dir: Path, tmp_path: Path, sprache: str
) -> None:
    """Im Betrieb prüft limits.check_language vorher; die Funktion selbst wirft PolicyError, nicht KeyError."""
    with pytest.raises(_policies().PolicyError) as info:
        _policies().signature_section(full_dir, sprache)
    _assert_fixed_error(info.value, tmp_path, "signatur.md")


@pytest.mark.parametrize("heading", ["## Deutsch", "## Englisch", "## Regeln"])
def test_a_signature_with_a_duplicated_section_is_unavailable(
    full_dir: Path, tmp_path: Path, heading: str
) -> None:
    """Ein doppelter Abschnitt wird nicht zusammengeführt, sondern ist ein Fehler (auch bei der anderen Sprache)."""
    path = full_dir / "signatur.md"
    path.write_text(
        path.read_text(encoding="utf-8") + f"\n{heading}\nZweiter Abschnitt\n", encoding="utf-8"
    )
    with pytest.raises(_policies().PolicyError) as info:
        _policies().signature_section(full_dir, "de")
    _assert_fixed_error(info.value, tmp_path, "signatur.md")
    assert _policies().verify_policies(full_dir) == ["richtlinie-abschnitt:signatur"]


# Byte Order Mark


def test_read_policy_accepts_a_byte_order_mark(empty_dir: Path) -> None:
    (empty_dir / "tonalitaet.md").write_bytes(b"\xef\xbb\xbf" + b"Text\n")
    text = _policies().read_policy(empty_dir, "tonalitaet.md")
    assert text == "Text\n"
    assert chr(0xFEFF) not in text


def test_a_file_with_only_a_byte_order_mark_is_unavailable(empty_dir: Path, tmp_path: Path) -> None:
    (empty_dir / "tonalitaet.md").write_bytes(b"\xef\xbb\xbf\n")
    with pytest.raises(_policies().PolicyError) as info:
        _policies().read_policy(empty_dir, "tonalitaet.md")
    _assert_fixed_error(info.value, tmp_path, "tonalitaet.md")


# Symlinks


def _symlink_or_skip(link: Path, target: Path) -> None:
    """Legt den Symlink an; ohne Recht (zum Beispiel Windows ohne Entwicklermodus) wird der Test übersprungen."""
    try:
        os.symlink(target, link)
    except (OSError, NotImplementedError):
        pytest.skip("Symlinks sind in dieser Umgebung nicht erlaubt")


def test_read_policy_refuses_a_symlink_out_of_the_folder(full_dir: Path, tmp_path: Path) -> None:
    """Kein Auslesen einer Datei außerhalb des Richtlinien-Ordners über einen eingeschleusten Link."""
    secret = tmp_path / "geheim.md"
    secret.write_text("GEHEIM-INHALT-4711\n", encoding="utf-8")
    (full_dir / "tonalitaet.md").unlink()
    _symlink_or_skip(full_dir / "tonalitaet.md", secret)
    with pytest.raises(_policies().PolicyError) as info:
        _policies().read_policy(full_dir, "tonalitaet.md")
    _assert_fixed_error(info.value, tmp_path, "tonalitaet.md")
    assert "GEHEIM-INHALT-4711" not in str(info.value)
    assert _policies().verify_policies(full_dir) == ["richtlinie-fehlt:tonalitaet"]


def test_read_policy_accepts_a_symlink_inside_the_folder(full_dir: Path) -> None:
    """Gegenprobe zur Strenge: Nur ein Link, der aus dem Ordner herauszeigt, wird abgelehnt."""
    (full_dir / "tonalitaet.md").rename(full_dir / "tonalitaet-alt.md")
    _symlink_or_skip(full_dir / "tonalitaet.md", full_dir / "tonalitaet-alt.md")
    assert _policies().read_policy(full_dir, "tonalitaet.md") == _real_text("tonalitaet.md")


# verify_policies


def test_verify_policies_accepts_the_real_files() -> None:
    assert _policies().verify_policies(_policies().POLICY_DIR) == []


def test_verify_policies_accepts_a_complete_copy(full_dir: Path) -> None:
    assert _policies().verify_policies(full_dir) == []


def test_verify_policies_names_every_missing_file_in_a_fixed_order(
    empty_dir: Path, tmp_path: Path
) -> None:
    found = _policies().verify_policies(empty_dir)
    assert found == SHORT_MISSING
    assert str(tmp_path) not in " ".join(found)


@pytest.mark.parametrize(
    ("name", "short"),
    [
        ("tonalitaet.md", "richtlinie-fehlt:tonalitaet"),
        ("eskalation.md", "richtlinie-fehlt:eskalation"),
        ("rabatte.md", "richtlinie-fehlt:rabatte"),
        ("signatur.md", "richtlinie-fehlt:signatur"),
    ],
)
def test_verify_policies_names_the_one_missing_file(full_dir: Path, name: str, short: str) -> None:
    (full_dir / name).unlink()
    assert _policies().verify_policies(full_dir) == [short]


def test_verify_policies_counts_an_unreadable_file_as_missing(full_dir: Path) -> None:
    (full_dir / "rabatte.md").unlink()
    (full_dir / "rabatte.md").mkdir()
    assert _policies().verify_policies(full_dir) == ["richtlinie-fehlt:rabatte"]


@pytest.mark.parametrize("heading", ["## Deutsch", "## Englisch", "## Regeln"])
def test_verify_policies_checks_the_sections_of_the_signature(full_dir: Path, heading: str) -> None:
    path = full_dir / "signatur.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace(heading, "## Anderes"), encoding="utf-8"
    )
    assert _policies().verify_policies(full_dir) == ["richtlinie-abschnitt:signatur"]


# Start-Check in main()


def _control_start(monkeypatch: pytest.MonkeyPatch, failure: Exception | None = None) -> list[Any]:
    """Ersetzt die Rollenprüfung und uvicorn.run; gibt die uvicorn-Aufrufe zurück (wie test_role_check.py)."""
    import uvicorn
    from hoffmann_data import db

    run_calls: list[Any] = []

    def fake_verify(database: Any) -> None:
        if failure is not None:
            raise failure

    monkeypatch.setattr(db, "verify_read_only_role", fake_verify)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: run_calls.append(kwargs))
    return run_calls


def _start_env(token: str) -> dict[str, str]:
    return {"MCP_SERVER_TOKEN": token, "MCP_SERVER_DATABASE_URL": GOOD_READ_URL}


def test_start_stops_when_the_policy_files_are_missing(
    token: str,
    empty_dir: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hoffmann_data.__main__ import main

    monkeypatch.setattr(_policies(), "POLICY_DIR", empty_dir)
    run_calls = _control_start(monkeypatch)
    with pytest.raises(SystemExit) as info:
        main(_start_env(token))
    assert info.value.code == 1
    assert run_calls == [], "uvicorn.run darf ohne Richtlinien nicht laufen"
    captured = capsys.readouterr()
    for short in SHORT_MISSING:
        assert short in captured.err
    output = captured.out + captured.err
    for leaked in (str(tmp_path), tmp_path.name, token, GOOD_READ_URL):
        assert leaked not in output, leaked


def test_start_reports_a_broken_signature_section(
    token: str,
    full_dir: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hoffmann_data.__main__ import main

    path = full_dir / "signatur.md"
    path.write_text(
        path.read_text(encoding="utf-8").replace("## Englisch", "## X"), encoding="utf-8"
    )
    monkeypatch.setattr(_policies(), "POLICY_DIR", full_dir)
    run_calls = _control_start(monkeypatch)
    with pytest.raises(SystemExit) as info:
        main(_start_env(token))
    assert info.value.code == 1
    assert run_calls == []
    assert "richtlinie-abschnitt:signatur" in capsys.readouterr().err


def test_start_reports_a_duplicated_signature_section(
    token: str,
    full_dir: Path,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hoffmann_data.__main__ import main

    path = full_dir / "signatur.md"
    path.write_text(
        path.read_text(encoding="utf-8") + "\n## Deutsch\nZweiter Abschnitt\n", encoding="utf-8"
    )
    monkeypatch.setattr(_policies(), "POLICY_DIR", full_dir)
    run_calls = _control_start(monkeypatch)
    with pytest.raises(SystemExit) as info:
        main(_start_env(token))
    assert info.value.code == 1
    assert run_calls == [], "uvicorn.run darf mit einem doppelten Abschnitt nicht laufen"
    captured = capsys.readouterr()
    assert "richtlinie-abschnitt:signatur" in captured.err
    for leaked in (str(tmp_path), tmp_path.name, token, GOOD_READ_URL):
        assert leaked not in captured.out + captured.err, leaked


def test_start_with_the_real_policy_files_serves_and_prints_nothing_about_them(
    token: str, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    from hoffmann_data.__main__ import main

    run_calls = _control_start(monkeypatch)
    main(_start_env(token))
    assert len(run_calls) == 1, "main() muss bis uvicorn.run durchlaufen"
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == ""


def test_a_failed_role_check_stops_before_the_policy_check(
    token: str,
    empty_dir: Path,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hoffmann_data import db
    from hoffmann_data.__main__ import main

    monkeypatch.setattr(_policies(), "POLICY_DIR", empty_dir)
    run_calls = _control_start(monkeypatch, db.RoleCheckError(("rolle-ist-superuser",)))
    with pytest.raises(SystemExit) as info:
        main(_start_env(token))
    assert info.value.code == 1
    assert run_calls == []
    err = capsys.readouterr().err
    assert "rolle-ist-superuser" in err
    assert "richtlinie" not in err
