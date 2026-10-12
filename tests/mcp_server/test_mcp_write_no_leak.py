"""Tests: Weder das Schreib- noch das Lese-Token taucht in einer Meldung auf (F08, ADR 0005 Punkte 2 bis 4).

Gilt für Antworten beider Pfade (auch 401, 404, 413, 421, 403 und Werkzeugfehler), das Log (alle Logger), stdout,
stderr, die Darstellung der App und die Fehlerausgabe des Starts. Geprüft wird der ganze Wert und jeder Ausschnitt
von acht Zeichen. Die Weiche schreibt bei Anfragen nichts ins Log.

Ohne Datenbank. Rot, bis server.py und switch.py den Doppelbetrieb kennen (TDD).
"""

import logging
from typing import Any

import pytest
from mcp_testkit import (
    MAX_REQUEST_BODY_BYTES,
    MCP_PATH,
    RPC_HEADERS,
    WRITE_PATH,
    WRITE_TOKEN_NAME,
    body_of_size,
)
from starlette.testclient import TestClient

SWITCH_LOGGER = "hoffmann_data.switch"
BODY = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}


def _pieces(secret: str) -> set[str]:
    """Der ganze Wert und Ausschnitte von acht Zeichen."""
    return {secret, *(secret[i : i + 8] for i in range(0, len(secret) - 7, 4))}


def _bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


def _call(path: str, name: str, arguments: dict[str, Any] | None = None) -> dict[str, Any]:
    return {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "tools/call",
        "params": {"name": name, "arguments": arguments or {}},
    }


def _responses(client: TestClient, read_token: str, write_token: str) -> list[tuple[str, Any]]:
    """Anfragen an beide Pfade mit allen Arten von Antworten, die ein Token verraten könnten."""
    wrong = _bearer("w" * len(write_token))
    big = body_of_size(MAX_REQUEST_BODY_BYTES + 1, "create_lead")
    sent: list[tuple[str, Any]] = []

    def post(label: str, path: str, headers: dict[str, str] | None, **kwargs: Any) -> None:
        kwargs.setdefault("json", BODY)
        sent.append(
            (label, client.post(path, headers={**RPC_HEADERS, **(headers or {})}, **kwargs))
        )

    for path, own, other in (
        (MCP_PATH, read_token, write_token),
        (WRITE_PATH, write_token, read_token),
    ):
        post(f"{path} richtig", path, _bearer(own))
        post(f"{path} fremdes-token", path, _bearer(other))
        post(f"{path} falsch", path, wrong)
        post(f"{path} fehlt", path, None)
        post(f"{path} host", path, {**_bearer(own), "Host": "evil.example"})
        post(f"{path} origin", path, {**_bearer(own), "Origin": "http://evil.example"})
    post(
        "write platzhalter",
        WRITE_PATH,
        _bearer(write_token),
        json=_call(WRITE_PATH, "create_lead", {"query": "x"}),
    )
    post("write unbekannt", WRITE_PATH, _bearer(write_token), json=_call(WRITE_PATH, "send_email"))
    post(
        "write lese-werkzeug",
        WRITE_PATH,
        _bearer(write_token),
        json=_call(WRITE_PATH, "search_products", {"query": "x"}),
    )
    post(
        "write resource",
        WRITE_PATH,
        _bearer(write_token),
        json={**BODY, "method": "resources/read", "params": {"uri": "policy://rabatte"}},
    )
    post(
        "read schreib-werkzeug", MCP_PATH, _bearer(read_token), json=_call(MCP_PATH, "create_lead")
    )
    post(
        "read resource",
        MCP_PATH,
        _bearer(read_token),
        json={**BODY, "method": "resources/read", "params": {"uri": "policy://rabatte"}},
    )
    post("write gross", WRITE_PATH, _bearer(write_token), content=big, json=None)
    post("write gross fremd", WRITE_PATH, _bearer(read_token), content=big, json=None)
    post("read gross", MCP_PATH, _bearer(read_token), content=big, json=None)
    for path, auth in (
        (MCP_PATH + "x", read_token),
        ("/mcp-write/", write_token),
        ("//mcp-write", read_token),
        ("/foo", write_token),
    ):
        post(f"unbekannt {path}", path, _bearer(auth))
    return sent


def test_neither_token_is_in_any_response_log_or_output(
    dual_client: TestClient,
    token: str,
    write_token: str,
    caplog: pytest.LogCaptureFixture,
    capsys: pytest.CaptureFixture[str],
) -> None:
    caplog.set_level(logging.DEBUG)
    responses = _responses(dual_client, token, write_token)
    assert len(responses) > 20
    secrets = {"Lese-Token": _pieces(token), "Schreib-Token": _pieces(write_token)}
    for label, response in responses:
        haystack = response.text + "\n" + str(dict(response.headers))
        for name, pieces in secrets.items():
            for piece in pieces:
                assert piece not in haystack, f"{name} in der Antwort: {label}"
    logged = "\n".join(f"{r.getMessage()} {r.exc_text or ''}" for r in caplog.records)
    captured = capsys.readouterr()
    for where, text in {"Log": logged, "stdout": captured.out, "stderr": captured.err}.items():
        for name, pieces in secrets.items():
            for piece in pieces:
                assert piece not in text, f"{name} in {where}"


def test_the_write_token_is_never_accepted_in_a_response_to_the_read_path_and_vice_versa(
    dual_client: TestClient, token: str, write_token: str
) -> None:
    """Die Antworten eines Pfads enthalten nie das Token des anderen, auch nicht, wenn der Client es mitschickt."""
    for path, mine, theirs in ((MCP_PATH, token, write_token), (WRITE_PATH, write_token, token)):
        response = dual_client.post(path, json=BODY, headers={**RPC_HEADERS, **_bearer(theirs)})
        assert response.status_code == 401
        for piece in _pieces(theirs) | _pieces(mine):
            assert piece not in response.text and piece not in str(dict(response.headers))


def test_the_switch_writes_nothing_to_the_log_for_requests(
    dual_client: TestClient, token: str, write_token: str, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)
    _responses(dual_client, token, write_token)
    assert [r for r in caplog.records if r.name == SWITCH_LOGGER] == []


def test_the_app_does_not_reveal_the_tokens_in_repr_or_str(
    dual_app: Any, token: str, write_token: str
) -> None:
    for text in (repr(dual_app), str(dual_app)):
        for secret in (token, write_token):
            for piece in _pieces(secret):
                assert piece not in text


def test_the_config_does_not_reveal_the_tokens_in_repr_or_str(
    write_env: dict[str, str], token: str, write_token: str
) -> None:
    from hoffmann_data.config import load_config

    config = load_config(write_env)
    for text in (repr(config), str(config)):
        for secret in (token, write_token):
            for piece in _pieces(secret):
                assert piece not in text


def test_start_error_output_does_not_contain_the_write_token(
    write_env: dict[str, str], capsys: pytest.CaptureFixture[str]
) -> None:
    from hoffmann_data.__main__ import main

    bad = "k" * 10 + " " + "k" * 30
    with pytest.raises(SystemExit) as info:
        main({**write_env, WRITE_TOKEN_NAME: bad})
    assert info.value.code == 1
    captured = capsys.readouterr()
    assert WRITE_TOKEN_NAME in captured.err
    assert "k" * 10 not in captured.out + captured.err


def test_a_valid_start_with_the_write_path_prints_no_token(
    write_env: dict[str, str],
    token: str,
    write_token: str,
    capsys: pytest.CaptureFixture[str],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """main() baut die echte App mit der Weiche (nur Prüfungen und uvicorn.run sind ersetzt) und druckt kein Token."""
    import uvicorn
    from hoffmann_data import db
    from hoffmann_data.__main__ import main

    apps: list[Any] = []
    monkeypatch.setattr(db, "verify_read_only_role", lambda database: None)
    monkeypatch.setattr(db, "verify_write_role", lambda database: None)
    monkeypatch.setattr(uvicorn, "run", lambda app, **kwargs: apps.append(app))
    main(write_env)
    assert len(apps) == 1
    captured = capsys.readouterr()
    for text in (captured.out, captured.err, repr(apps[0])):
        for secret in (token, write_token):
            for piece in _pieces(secret):
                assert piece not in text
