"""Tests der Größenbegrenzung von Anfragen (F06): höchstens 256 KiB je Anfrage.

Der Body-Limit-Wächter des SDKs (RequestBodyLimitMiddleware) antwortet mit 413. Die Token-Prüfung sitzt
außen: Ohne gültiges Token bleibt es bei 401, auch bei großem Body.
"""

import json
from typing import Any

from mcp_testkit import (
    MAX_REQUEST_BODY_BYTES,
    MCP_PATH,
    NOT_IMPLEMENTED,
    RPC_HEADERS,
    parse_rpc_body,
)
from starlette.testclient import TestClient


def _body_of_size(size: int) -> bytes:
    """Gültiger JSON-RPC-Aufruf eines Platzhalter-Werkzeugs mit genau `size` Bytes (nur ASCII)."""

    def build(pad: int) -> bytes:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "search_products", "arguments": {"query": "x" * pad}},
        }
        return json.dumps(payload).encode("ascii")

    overhead = len(build(0))
    body = build(size - overhead)
    assert len(body) == size
    return body


def _post(client: TestClient, body: bytes, headers: dict[str, str] | None = None) -> Any:
    return client.post(MCP_PATH, content=body, headers={**RPC_HEADERS, **(headers or {})})


def test_body_just_under_the_limit_is_accepted(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    response = _post(client, _body_of_size(MAX_REQUEST_BODY_BYTES - 1), auth_headers)
    assert response.status_code == 200
    assert NOT_IMPLEMENTED in json.dumps(parse_rpc_body(response), ensure_ascii=False)


def test_body_over_the_limit_returns_413(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = _post(client, _body_of_size(MAX_REQUEST_BODY_BYTES + 1), auth_headers)
    assert response.status_code == 413


def test_much_larger_body_returns_413(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = _post(client, _body_of_size(1024 * 1024), auth_headers)
    assert response.status_code == 413


def test_large_body_without_token_stays_401(client: TestClient) -> None:
    for size in (MAX_REQUEST_BODY_BYTES - 1, MAX_REQUEST_BODY_BYTES + 1, 1024 * 1024):
        assert _post(client, _body_of_size(size)).status_code == 401


def test_large_body_with_wrong_token_stays_401(client: TestClient, token: str) -> None:
    wrong = {"Authorization": "Bearer " + "q" * len(token)}
    assert _post(client, _body_of_size(MAX_REQUEST_BODY_BYTES + 1), wrong).status_code == 401
