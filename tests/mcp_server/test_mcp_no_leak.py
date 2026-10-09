"""Tests: Das Token taucht in keiner Meldung auf, und der Datenbankzugriff bleibt an einer Stelle (F06, F07).

Seit F07 gibt es genau ein Modul mit Datenbanktreiber: hoffmann_data/db.py. Alle anderen Module des Pakets
bekommen die Verbindung von dort und importieren psycopg nie selbst.
"""

import ast
import logging
import re
from pathlib import Path
from typing import Any

import pytest
from mcp_testkit import MCP_PATH, MCP_SERVER_DIR, RPC_HEADERS
from starlette.testclient import TestClient

BODY = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}


@pytest.mark.parametrize(
    "secret",
    [
        "kurz-und-geheim",
        "x" * 20 + " " + "y" * 20,
        "z" * 40 + "\n",
    ],
    ids=["zu-kurz", "leerzeichen", "zeilenumbruch"],
)
def test_config_errors_do_not_contain_the_token(secret: str) -> None:
    from hoffmann_data.config import ConfigError, load_config

    with pytest.raises(ConfigError) as info:
        load_config({"MCP_SERVER_TOKEN": secret})
    message = str(info.value)
    assert secret.strip() not in message
    assert secret not in message
    assert repr(secret) not in message


def test_start_error_output_does_not_contain_the_token(
    capsys: pytest.CaptureFixture[str],
) -> None:
    from hoffmann_data.__main__ import main

    secret = "k" * 10 + " " + "k" * 30
    with pytest.raises(SystemExit):
        main({"MCP_SERVER_TOKEN": secret})
    captured = capsys.readouterr()
    assert "k" * 10 not in captured.out + captured.err


def test_token_is_in_no_response_and_no_log(
    client: TestClient,
    token: str,
    auth_headers: dict[str, str],
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    wrong = "w" * len(token)
    caplog.set_level(logging.DEBUG)

    responses: list[Any] = [
        client.post(MCP_PATH, json=BODY, headers=RPC_HEADERS),
        client.post(
            MCP_PATH, json=BODY, headers={**RPC_HEADERS, "Authorization": f"Bearer {wrong}"}
        ),
        client.post(
            MCP_PATH, json=BODY, headers={**RPC_HEADERS, "Host": "evil.example", **auth_headers}
        ),
        client.post(
            MCP_PATH,
            json={
                **BODY,
                "method": "tools/call",
                "params": {"name": "get_product", "arguments": {}},
            },
            headers={**RPC_HEADERS, **auth_headers},
        ),
        client.post(
            MCP_PATH,
            json={**BODY, "method": "resources/read", "params": {"uri": "policy://rabatte"}},
            headers={**RPC_HEADERS, **auth_headers},
        ),
    ]

    for response in responses:
        assert token not in response.text
        assert wrong not in response.text
        assert token not in str(dict(response.headers))
        assert wrong not in str(dict(response.headers))

    logged = "\n".join(f"{r.getMessage()} {r.exc_text or ''}" for r in caplog.records)
    assert token not in logged
    assert wrong not in logged
    captured = capsys.readouterr()
    assert token not in captured.out + captured.err
    assert wrong not in captured.out + captured.err


def test_config_object_does_not_reveal_the_token_in_repr(token: str) -> None:
    from hoffmann_data.config import load_config

    config = load_config({"MCP_SERVER_TOKEN": token})
    assert token not in repr(config)
    assert token not in str(config)


PACKAGE = Path(MCP_SERVER_DIR) / "hoffmann_data"
DB_MODULE = PACKAGE / "db.py"


def _sources() -> list[Path]:
    sources = sorted(PACKAGE.rglob("*.py"))
    assert sources, "mcp-server/hoffmann_data enthält noch keinen Code"
    return sources


def _parse(source: Path) -> ast.AST:
    return ast.parse(source.read_text(encoding="utf-8"), filename=str(source))


def _imported_modules(tree: ast.AST) -> list[str]:
    """Namen aller importierten Module (absolut), aus den Importknoten, nicht aus dem Text."""
    modules: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            modules.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            modules.append(node.module)
    return modules


def _is_driver(module: str) -> bool:
    return module.split(".")[0].startswith("psycopg")


def test_only_the_db_module_imports_the_database_driver() -> None:
    """Der Treiber (psycopg, auch psycopg_pool u. a.) wird nur in db.py importiert."""
    offenders = [
        str(source.relative_to(PACKAGE))
        for source in _sources()
        if source != DB_MODULE and any(_is_driver(m) for m in _imported_modules(_parse(source)))
    ]
    assert not offenders, f"Treiberimport außerhalb von db.py: {offenders}"


def test_db_module_imports_psycopg_and_connects_through_it() -> None:
    """db.py importiert psycopg als Modul und ruft psycopg.connect auf (so greift der Zähltest der Verbindungen)."""
    assert DB_MODULE.is_file(), "hoffmann_data/db.py fehlt"
    tree = _parse(DB_MODULE)
    assert "psycopg" in _imported_modules(tree)
    plain_import = any(
        isinstance(node, ast.Import)
        and any(a.name == "psycopg" and a.asname is None for a in node.names)
        for node in ast.walk(tree)
    )
    assert plain_import, "import psycopg (ohne Alias), damit psycopg.connect im Test ersetzbar ist"
    connects = [
        node
        for node in ast.walk(tree)
        if isinstance(node, ast.Call)
        and isinstance(node.func, ast.Attribute)
        and node.func.attr == "connect"
        and isinstance(node.func.value, ast.Name)
        and node.func.value.id == "psycopg"
    ]
    assert connects, "db.py ruft psycopg.connect nicht auf"


def test_package_has_no_dynamic_imports() -> None:
    """Kein importlib und kein __import__: Der Treiber ließe sich sonst an der Prüfung vorbei laden."""
    for source in _sources():
        tree = _parse(source)
        assert "importlib" not in _imported_modules(tree), source
        calls = [
            node
            for node in ast.walk(tree)
            if isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == "__import__"
        ]
        assert not calls, source


def test_package_does_not_use_the_seed_variable_or_an_async_driver() -> None:
    """Die Variable des Seed-Skripts (DATABASE_URL) und asyncpg bleiben im ganzen Paket verboten."""
    for source in _sources():
        text = source.read_text(encoding="utf-8")
        # Verboten ist nur das nackte Wort DATABASE_URL (die Variable des Seed-Skripts). \b trifft weder
        # MCP_SERVER_DATABASE_URL noch DATABASE_URL_VAR, weil der Unterstrich zum Wort gehört.
        assert not re.search(r"\bDATABASE_URL\b", text), source
        assert "asyncpg" not in text, source
