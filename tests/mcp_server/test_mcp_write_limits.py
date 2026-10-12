"""Tests der Größenbegrenzung von Anfragen auf /mcp-write (F08, ADR 0005 Punkt 3): höchstens 256 KiB je Anfrage.

Wiederholung von test_mcp_limits.py für den Schreib-Pfad: Der Body-Limit-Wächter des SDKs antwortet mit 413. Die
Token-Prüfung sitzt außen: Ohne gültiges Schreib-Token (auch mit dem Lese-Token) bleibt es bei 401, auch bei großem Body.
Das Limit gilt auch auf /mcp weiter, wenn der Schreibweg konfiguriert ist.

Ohne Datenbank. Rot, bis server.py und switch.py den Doppelbetrieb kennen (TDD).
"""

import json
from typing import Any

import pytest
from mcp_testkit import (
    MAX_REQUEST_BODY_BYTES,
    MCP_PATH,
    RPC_HEADERS,
    WRITE_PATH,
    body_of_size,
    parse_rpc_body,
)
from starlette.testclient import TestClient

TOOL = "create_lead"
# Die fünf übrigen Pflichtfelder von create_lead, gültig. Das aufgefüllte Feld ist company_name: Es ist das erste Feld,
# das das Werkzeug prüft, und ist hier zu lang. So erreicht der Aufruf die Eingabeprüfung in limits.py (feste Meldung,
# keine Datenbank) und löst nicht den SDK-Fehler für fehlende Argumente aus (Issue #21).
PAD_FIELD = "company_name"
VALID_REST = {
    "contact_first_name": "Erika",
    "contact_last_name": "Muster",
    "contact_email": "erika@firma.example",
    "language": "de",
    "country": "de",
}


def _body(size: int) -> bytes:
    return body_of_size(size, TOOL, field=PAD_FIELD, extra=VALID_REST)


def _post(client: TestClient, body: bytes, headers: dict[str, str] | None = None) -> Any:
    return client.post(WRITE_PATH, content=body, headers={**RPC_HEADERS, **(headers or {})})


def test_body_just_under_the_limit_is_accepted(
    dual_client: TestClient, write_headers: dict[str, str]
) -> None:
    """Die Anfrage erreicht das Werkzeug: Es antwortet mit seiner festen Meldung zum zu langen Firmennamen."""
    from hoffmann_data.limits import COMPANY_NAME_INVALID

    response = _post(dual_client, _body(MAX_REQUEST_BODY_BYTES - 1), write_headers)
    assert response.status_code == 200
    assert COMPANY_NAME_INVALID in json.dumps(parse_rpc_body(response), ensure_ascii=False)


def test_body_over_the_limit_returns_413(
    dual_client: TestClient, write_headers: dict[str, str]
) -> None:
    response = _post(dual_client, _body(MAX_REQUEST_BODY_BYTES + 1), write_headers)
    assert response.status_code == 413


def test_much_larger_body_returns_413(
    dual_client: TestClient, write_headers: dict[str, str]
) -> None:
    assert _post(dual_client, _body(1024 * 1024), write_headers).status_code == 413


def test_large_body_without_token_stays_401(dual_client: TestClient) -> None:
    for size in (MAX_REQUEST_BODY_BYTES - 1, MAX_REQUEST_BODY_BYTES + 1, 1024 * 1024):
        assert _post(dual_client, _body(size)).status_code == 401


def test_large_body_with_wrong_token_stays_401(dual_client: TestClient, write_token: str) -> None:
    wrong = {"Authorization": "Bearer " + "q" * len(write_token)}
    assert _post(dual_client, _body(MAX_REQUEST_BODY_BYTES + 1), wrong).status_code == 401


def test_large_body_with_the_read_token_stays_401(
    dual_client: TestClient, read_headers: dict[str, str]
) -> None:
    for size in (MAX_REQUEST_BODY_BYTES - 1, MAX_REQUEST_BODY_BYTES + 1):
        assert _post(dual_client, _body(size), read_headers).status_code == 401


@pytest.mark.parametrize("delta", [0, 1])
def test_the_limit_is_exactly_the_same_on_both_paths(
    dual_client: TestClient,
    read_headers: dict[str, str],
    write_headers: dict[str, str],
    delta: int,
) -> None:
    """Gleiche Grenze: Was auf einem Pfad gerade noch durchgeht, geht auf dem anderen auch durch (und umgekehrt 413)."""
    size = MAX_REQUEST_BODY_BYTES - 1 + delta * 2  # genau unter oder genau über dem Limit
    read = dual_client.post(
        MCP_PATH,
        content=body_of_size(size, "search_products"),
        headers={**RPC_HEADERS, **read_headers},
    )
    write = _post(dual_client, _body(size), write_headers)
    assert read.status_code == write.status_code == (200 if delta == 0 else 413)


def test_the_read_path_keeps_its_limit_in_dual_operation(
    dual_client: TestClient, read_headers: dict[str, str]
) -> None:
    for size, expected in ((MAX_REQUEST_BODY_BYTES + 1, 413), (1024 * 1024, 413)):
        response = dual_client.post(
            MCP_PATH,
            content=body_of_size(size, "search_products"),
            headers={**RPC_HEADERS, **read_headers},
        )
        assert response.status_code == expected
