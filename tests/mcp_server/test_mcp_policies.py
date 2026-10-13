"""Tests der beiden Resources des Lese-Pfads /mcp (F09, ADR 0006) über HTTP mit gültigem Lese-Token.

Wie test_mcp_interfaces.py: roher JSON-RPC-Weg durch Middleware, SDK-App und Transport (Fixture rpc), ohne Datenbank.
Für Fälle mit eigenem Richtlinien-Ordner baut das lokale Fixture rpc_for die App mit create_app(..., policy_dir=...).

Festgelegt:
- policy://tonalitaet liefert tonalitaet.md, policy://rabatte liefert RABATTE_PREAMBLE + eskalation.md +
  RABATTE_SEPARATOR + rabatte.md (policies.py), beide als text/markdown, je eine Inhaltsposition mit text.
- Der Vorspann steht vor jeder Tabellenzeile und sagt: intern, keine Zusage, nie in einem Antwortentwurf.
- Eine unbekannte URI ist ein Fehler mit Code -32602, keine Resource wird erfunden (signatur und eskalation sind
  keine eigenen Resources).
- Fehlt eine Datei, ist die Antwort Code -32603 mit der festen Meldung POLICY_UNAVAILABLE, ohne Pfad und Dateinamen,
  auch nicht in Log, stdout und stderr. Die Resources berühren die Datenbank nie. Der Schreib-Pfad hat keine.
- Auch eine zu große oder keine UTF-8-Datei ist nur diese eine feste Meldung (über HTTP belegt). Eine BOM am Anfang der
  Datei steht nicht im Text der Resource.
- Die Prozentwerte der Rabatt-Tabelle stehen in keiner der Dateien, die in den Entwurfsprompt eingehen (Drift-Test).
"""

import json
import logging
import re
import shutil
from collections.abc import Callable, Iterator
from contextlib import ExitStack
from pathlib import Path
from typing import Any

import pytest
from mcp_testkit import (
    BASE_URL,
    MCP_PATH,
    RESOURCE_URIS,
    ROOT,
    RPC_HEADERS,
    CountingDatabase,
    parse_rpc_body,
)
from starlette.testclient import TestClient

Rpc = Callable[..., dict[str, Any]]

REAL_DIR = ROOT / "data" / "richtlinien"
POLICY_FILES = ("tonalitaet.md", "eskalation.md", "rabatte.md", "signatur.md")
INVALID_PARAMS_CODE = -32602
INTERNAL_ERROR_CODE = -32603
MARKDOWN = "text/markdown"
# Sätze des Vorspanns von policy://rabatte (Kennzeichnung "intern, keine Zusage, nie im Entwurf")
PREAMBLE_PHRASES = (
    "INTERN",
    "keine Zusage",
    "nie in einem Antwortentwurf",
    "gerundet, umschrieben oder als Spanne",
    "nie zugesagt",
)


def _real_text(name: str) -> str:
    return (REAL_DIR / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def _policies() -> Any:
    from hoffmann_data import policies

    return policies


def _expected(uri: str) -> str:
    policies = _policies()
    if uri == "policy://tonalitaet":
        return _real_text("tonalitaet.md")
    return (
        policies.RABATTE_PREAMBLE
        + _real_text("eskalation.md")
        + policies.RABATTE_SEPARATOR
        + _real_text("rabatte.md")
    )


@pytest.fixture
def copied_dir(tmp_path: Path) -> Path:
    directory = tmp_path / "richtlinien"
    directory.mkdir()
    for name in POLICY_FILES:
        shutil.copyfile(REAL_DIR / name, directory / name)
    return directory


@pytest.fixture
def rpc_for(token: str) -> Iterator[Callable[..., Rpc]]:
    """Fabrik: baut die App mit eigenem Richtlinien-Ordner (und optional einer Datenbank) und gibt `rpc` dafür zurück."""
    from hoffmann_data.config import load_config
    from hoffmann_data.server import create_app

    with ExitStack() as stack:

        def _for(directory: Path, database: Any = None) -> Rpc:
            app = create_app(
                load_config({"MCP_SERVER_TOKEN": token}), database, policy_dir=directory
            )
            client = stack.enter_context(TestClient(app, base_url=BASE_URL))
            headers = {**RPC_HEADERS, "Authorization": f"Bearer {token}"}
            counter = iter(range(1, 10_000))

            def _call(method: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
                payload = {
                    "jsonrpc": "2.0",
                    "id": next(counter),
                    "method": method,
                    "params": params or {},
                }
                response = client.post(MCP_PATH, json=payload, headers=headers)
                assert response.status_code == 200, response.text
                return parse_rpc_body(response)

            return _call

        yield _for


# Liste


def _resources(rpc: Rpc) -> dict[str, dict[str, Any]]:
    return {str(r["uri"]): r for r in rpc("resources/list")["result"]["resources"]}


def test_both_resources_are_listed_as_markdown_with_a_real_description(rpc: Rpc) -> None:
    resources = _resources(rpc)
    assert set(resources) == RESOURCE_URIS
    for uri, item in resources.items():
        assert item["mimeType"] == MARKDOWN, uri
        assert item["name"].strip(), uri
        description = item["description"]
        assert description.strip(), uri
        assert "platzhalter" not in description.lower(), uri


def test_the_discount_resource_is_described_as_internal(rpc: Rpc) -> None:
    assert "INTERN" in _resources(rpc)["policy://rabatte"]["description"]


# Lesen


@pytest.mark.parametrize("uri", sorted(RESOURCE_URIS))
def test_each_resource_returns_its_text_as_one_markdown_item(rpc: Rpc, uri: str) -> None:
    body = rpc("resources/read", {"uri": uri})
    assert "error" not in body, body
    contents = body["result"]["contents"]
    assert len(contents) == 1
    item = contents[0]
    assert str(item["uri"]) == uri
    assert item["mimeType"] == MARKDOWN
    assert "blob" not in item
    assert item["text"] == _expected(uri)


def test_the_discount_resource_has_the_preamble_before_the_first_table_row(rpc: Rpc) -> None:
    text = rpc("resources/read", {"uri": "policy://rabatte"})["result"]["contents"][0]["text"]
    table = text.index("| Kundenstatus |")
    for phrase in PREAMBLE_PHRASES:
        assert phrase in text, phrase
        assert text.index(phrase) < table, f"{phrase!r} steht erst nach der Tabelle"


def test_the_discount_resource_contains_escalation_rules_before_the_table(rpc: Rpc) -> None:
    text = rpc("resources/read", {"uri": "policy://rabatte"})["result"]["contents"][0]["text"]
    assert text.index("# Eskalationsregeln") < text.index("# Rabattregeln (intern)")


def _discount_values() -> set[str]:
    """Die Prozentwerte der Tabelle in rabatte.md (vierte Spalte, zum Beispiel "2,50")."""
    values: set[str] = set()
    for line in _real_text("rabatte.md").split("\n"):
        cells = [cell.strip() for cell in line.strip().strip("|").split("|")]
        if line.startswith("|") and len(cells) >= 4 and re.fullmatch(r"\d+,\d+", cells[3]):
            values.add(cells[3])
    return values


def _mentions(text: str, value: str) -> bool:
    """Der Wert steht als Zahl im Text, oder als Prozentangabe ohne die Nullen am Ende ("3 %", "2,5 Prozent")."""
    short = value.rstrip("0").rstrip(",")
    exact = re.search(rf"(?<![\d,.]){re.escape(value)}(?![\d,])", text)
    percent = re.search(rf"(?<![\d,.]){re.escape(short)}\s?(?:%|Prozent)", text)
    return bool(exact or percent)


def test_prompt_files_contain_no_value_of_the_discount_table() -> None:
    """tonalitaet.md und signatur.md gehen vollständig in Nachricht 1 ein; kein Tabellenwert darf dort stehen.

    Ein Beispiel wie "Gewähre 50 %" (tonalitaet.md) ist kein Tabellenwert, solange die Tabelle diese Zahl nicht enthält.
    """
    values = _discount_values()
    assert len(values) >= 5, "die Tabelle wurde nicht gelesen"
    for name in ("tonalitaet.md", "signatur.md"):
        text = _real_text(name)
        for value in sorted(values):
            assert not _mentions(text, value), f"der Tabellenwert {value} steht in {name}"


def test_the_discount_value_check_finds_an_inserted_value() -> None:
    """Gegenprobe: Die Prüfung schlägt an, wenn ein Wert (als Zahl oder als Prozentangabe) eingefügt wird."""
    values = _discount_values()
    base = _real_text("tonalitaet.md")
    assert any(_mentions(base + "\nBis zu 2,50 Zeilen", value) for value in values)
    assert any(_mentions(base + "\nBis zu 3 % Nachlass", value) for value in values)
    assert not any(_mentions(base, value) for value in values)


@pytest.mark.parametrize(
    "uri", ["policy://gibt-es-nicht", "policy://signatur", "policy://eskalation"]
)
def test_an_unknown_uri_is_an_invalid_params_error_without_data(rpc: Rpc, uri: str) -> None:
    body = rpc("resources/read", {"uri": uri})
    assert "result" not in body
    assert body["error"]["code"] == INVALID_PARAMS_CODE


def test_the_files_are_read_on_every_call(copied_dir: Path, rpc_for: Callable[..., Rpc]) -> None:
    rpc = rpc_for(copied_dir)
    first = rpc("resources/read", {"uri": "policy://tonalitaet"})["result"]["contents"][0]["text"]
    (copied_dir / "tonalitaet.md").write_text("Neue Fassung", encoding="utf-8")
    second = rpc("resources/read", {"uri": "policy://tonalitaet"})["result"]["contents"][0]["text"]
    assert first == _real_text("tonalitaet.md")
    assert second == "Neue Fassung"


# Fehlende Dateien

AFFECTED = {
    "tonalitaet.md": {"policy://tonalitaet"},
    "eskalation.md": {"policy://rabatte"},
    "rabatte.md": {"policy://rabatte"},
    "signatur.md": set(),  # gehört nur zum Prompt, nicht zu den Resources
}


@pytest.mark.parametrize("missing", POLICY_FILES)
def test_a_missing_file_gives_the_fixed_message_only_for_the_resources_that_need_it(
    copied_dir: Path,
    tmp_path: Path,
    rpc_for: Callable[..., Rpc],
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
    missing: str,
) -> None:
    caplog.set_level(logging.DEBUG)
    (copied_dir / missing).unlink()
    rpc = rpc_for(copied_dir)
    # Erst die Resources, die die Datei brauchen: Jede wird allein aufgerufen, damit Fehlerantwort, Log und Ausgabe
    # genau dieses Aufrufs geprüft werden. Das Log des SDK (DEBUG) enthält sonst auch die Texte erfolgreicher Antworten,
    # und die nennen andere Dateien beim Namen (z. B. "siehe signatur.md").
    for uri in sorted(AFFECTED[missing]):
        caplog.clear()
        capsys.readouterr()
        body = rpc("resources/read", {"uri": uri})
        assert "result" not in body, uri
        assert body["error"]["code"] == INTERNAL_ERROR_CODE
        assert body["error"]["message"] == _policies().POLICY_UNAVAILABLE
        captured = capsys.readouterr()
        logged = "\n".join(f"{r.getMessage()} {r.exc_text or ''}" for r in caplog.records)
        everything = (
            json.dumps(body["error"], ensure_ascii=False) + captured.out + captured.err + logged
        )
        for leaked in (str(tmp_path), tmp_path.name, copied_dir.name, missing):
            assert leaked not in everything, (
                f"{leaked!r} steht in der Fehlerantwort, im Log oder in der Ausgabe"
            )
    # Dann die übrigen: erfolgreich mit dem erwarteten Text (ohne Prüfung auf Dateinamen im Text)
    for uri in sorted(RESOURCE_URIS - AFFECTED[missing]):
        body = rpc("resources/read", {"uri": uri})
        assert "error" not in body, uri
        assert body["result"]["contents"][0]["text"] == _expected(uri)


def test_an_empty_folder_makes_both_resources_unavailable(
    tmp_path: Path, rpc_for: Callable[..., Rpc]
) -> None:
    empty = tmp_path / "leer"
    empty.mkdir()
    rpc = rpc_for(empty)
    for uri in sorted(RESOURCE_URIS):
        body = rpc("resources/read", {"uri": uri})
        assert body["error"]["message"] == _policies().POLICY_UNAVAILABLE


@pytest.mark.parametrize(
    "content",
    ["gross", "kein-utf8"],
)
def test_an_unusable_policy_file_makes_the_resource_unavailable(
    copied_dir: Path, tmp_path: Path, rpc_for: Callable[..., Rpc], content: str
) -> None:
    """Zu große Datei und Datei ohne gültiges UTF-8: dieselbe feste Meldung über HTTP, kein Pfad, kein Dateiname.

    Der Lader ist in test_policies_files.py getestet; hier die Zuordnung zur Fehlerantwort der Resource.
    """
    data = b"x" * (_policies().MAX_POLICY_BYTES + 1) if content == "gross" else b"\xff\xfe\xfd"
    (copied_dir / "tonalitaet.md").write_bytes(data)
    rpc = rpc_for(copied_dir)
    body = rpc("resources/read", {"uri": "policy://tonalitaet"})
    assert "result" not in body
    assert body["error"]["code"] == INTERNAL_ERROR_CODE
    assert body["error"]["message"] == _policies().POLICY_UNAVAILABLE
    for leaked in (str(tmp_path), tmp_path.name, "tonalitaet.md"):
        assert leaked not in json.dumps(body["error"], ensure_ascii=False), leaked
    # Die andere Resource bleibt lesbar
    other = rpc("resources/read", {"uri": "policy://rabatte"})
    assert other["result"]["contents"][0]["text"] == _expected("policy://rabatte")


def test_a_byte_order_mark_does_not_reach_the_resource_text(
    copied_dir: Path, rpc_for: Callable[..., Rpc]
) -> None:
    (copied_dir / "tonalitaet.md").write_bytes(
        b"\xef\xbb\xbf" + (REAL_DIR / "tonalitaet.md").read_bytes()
    )
    body = rpc_for(copied_dir)("resources/read", {"uri": "policy://tonalitaet"})
    text = body["result"]["contents"][0]["text"]
    assert chr(0xFEFF) not in text
    assert text == _real_text("tonalitaet.md")


# Keine Datenbank, Schreib-Pfad


def test_resources_never_touch_the_database(copied_dir: Path, rpc_for: Callable[..., Rpc]) -> None:
    database = CountingDatabase()
    rpc = rpc_for(copied_dir, database)
    rpc("resources/list")
    for uri in sorted(RESOURCE_URIS):
        assert "error" not in rpc("resources/read", {"uri": uri})
    assert database.calls == 0


def test_the_write_path_lists_neither_resources_nor_prompts(
    rpc_write: Rpc,
) -> None:
    assert rpc_write("resources/list")["result"]["resources"] == []
    assert rpc_write("prompts/list")["result"]["prompts"] == []
