"""Tests des Zugangsschutzes (F06): ohne gültiges Token antwortet der Server nie mit Inhalt."""

from collections.abc import Callable
from typing import Any

import pytest
from mcp_testkit import MCP_PATH, RPC_HEADERS
from starlette.testclient import TestClient

TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}


def _post(client: TestClient, headers: dict[str, str] | None = None, path: str = MCP_PATH) -> Any:
    return client.post(path, json=TOOLS_LIST, headers={**RPC_HEADERS, **(headers or {})})


def test_without_token_returns_401(client: TestClient) -> None:
    response = _post(client)
    assert response.status_code == 401
    assert response.headers["www-authenticate"] == "Bearer"


def test_wrong_token_returns_401(client: TestClient, token: str) -> None:
    wrong = ("x" if token[0] != "x" else "y") + token[1:]
    assert len(wrong) == len(token)
    assert _post(client, {"Authorization": f"Bearer {wrong}"}).status_code == 401


@pytest.mark.parametrize(
    "variant",
    ["prefix", "longer", "empty", "swapped-case"],
)
def test_near_miss_tokens_return_401(client: TestClient, token: str, variant: str) -> None:
    attempts = {
        "prefix": token[:-1],
        "longer": token + "x",
        "empty": "",
        "swapped-case": token.swapcase(),
    }
    assert _post(client, {"Authorization": f"Bearer {attempts[variant]}"}).status_code == 401


@pytest.mark.parametrize("scheme", ["Basic", "Token", "Digest"])
def test_other_schemes_return_401(client: TestClient, token: str, scheme: str) -> None:
    assert _post(client, {"Authorization": f"{scheme} {token}"}).status_code == 401


@pytest.mark.parametrize(
    "form",
    ["Bearer  {t}", "Bearer\t{t}", "bearer {t}", "BEARER {t}", " Bearer {t}", "Bearer {t} "],
    ids=[
        "zwei-leerzeichen",
        "tabulator",
        "klein",
        "gross",
        "leerzeichen-vorn",
        "leerzeichen-hinten",
    ],
)
def test_correct_token_in_wrong_header_form_returns_401(
    client: TestClient, token: str, form: str
) -> None:
    """Akzeptiert wird nur genau "Bearer " + Token, Schreibweise und Abstände ändern wir nicht."""
    assert _post(client, {"Authorization": form.format(t=token)}).status_code == 401


def test_websocket_connection_without_token_fails(client: TestClient) -> None:
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(MCP_PATH):
            pass


def test_token_without_scheme_returns_401(client: TestClient, token: str) -> None:
    assert _post(client, {"Authorization": token}).status_code == 401


def test_bearer_without_value_returns_401(client: TestClient) -> None:
    assert _post(client, {"Authorization": "Bearer"}).status_code == 401
    assert _post(client, {"Authorization": "Bearer "}).status_code == 401


def test_token_in_query_parameter_is_not_accepted(client: TestClient, token: str) -> None:
    assert _post(client, path=f"{MCP_PATH}?token={token}").status_code == 401
    assert _post(client, path=f"{MCP_PATH}?access_token={token}").status_code == 401


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/foo",
        "/mcp/",
        "/sse",
        "/health",
        "/.well-known/oauth-protected-resource",
        "/authorize",
    ],
)
def test_every_path_requires_the_token(client: TestClient, path: str) -> None:
    """Keine Route ohne Token, auch nicht für Pfade, die es nicht gibt (kein 404 ohne Token)."""
    for method in ("GET", "POST", "DELETE"):
        response = client.request(method, path)
        assert response.status_code == 401, f"{method} {path}"


def test_correct_token_is_accepted(client: TestClient, auth_headers: dict[str, str]) -> None:
    response = _post(client, auth_headers)
    assert response.status_code == 200


def test_missing_and_wrong_token_answers_are_identical(client: TestClient) -> None:
    """Die Antwort verrät nicht, ob das Token fehlte oder falsch war."""
    missing = _post(client)
    wrong = _post(client, {"Authorization": "Bearer " + "z" * 43})
    assert missing.status_code == wrong.status_code == 401
    assert missing.content == wrong.content
    assert dict(missing.headers) == dict(wrong.headers)


def test_unauthorized_answer_has_no_oauth_hints(client: TestClient) -> None:
    """Kein OAuth: weder resource_metadata noch error_description in der Antwort."""
    response = _post(client)
    challenge = response.headers["www-authenticate"].lower()
    assert "resource_metadata" not in challenge
    assert "error_description" not in challenge
    assert "oauth" not in response.text.lower()


def test_comparison_uses_hmac_compare_digest(
    client: TestClient, token: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Vergleich in konstanter Zeit: hmac.compare_digest wird mit Bytes aufgerufen."""
    import hmac

    from hoffmann_data import auth

    calls: list[tuple[Any, Any]] = []
    original: Callable[[Any, Any], bool] = hmac.compare_digest

    def spy(a: Any, b: Any) -> bool:
        calls.append((a, b))
        return original(a, b)

    monkeypatch.setattr(auth.hmac, "compare_digest", spy)

    _post(client, {"Authorization": "Bearer " + "q" * len(token)})

    assert calls, "hmac.compare_digest wurde nicht aufgerufen"
    assert all(isinstance(a, bytes) and isinstance(b, bytes) for a, b in calls)


def test_host_and_origin_are_checked_after_valid_token(
    client: TestClient, auth_headers: dict[str, str]
) -> None:
    """Der Host-/Origin-Schutz des SDKs ist an (nur Loopback-Host erlaubt)."""
    bad_host = _post(client, {**auth_headers, "Host": "evil.example"})
    assert bad_host.status_code == 421

    bad_origin = _post(client, {**auth_headers, "Origin": "http://evil.example"})
    assert bad_origin.status_code == 403

    good_origin = _post(client, {**auth_headers, "Origin": "http://localhost:3000"})
    assert good_origin.status_code == 200


def test_host_check_uses_allowed_hosts_for_non_loopback(
    make_app: Callable[..., Any], token: str
) -> None:
    """Auf 0.0.0.0 gilt nur die ausdrücklich erlaubte Host-Liste, nicht die Loopback-Voreinstellung."""
    app = make_app(
        {
            "MCP_SERVER_TOKEN": token,
            "MCP_SERVER_HOST": "0.0.0.0",
            "MCP_SERVER_ALLOWED_HOSTS": "daten.example.org",
        }
    )
    headers = {"Authorization": f"Bearer {token}"}
    with TestClient(app, base_url="http://daten.example.org") as c:
        assert _post(c, headers).status_code == 200
        assert _post(c, {**headers, "Host": "evil.example"}).status_code == 421
        assert _post(c, {**headers, "Host": "127.0.0.1:8000"}).status_code == 421
