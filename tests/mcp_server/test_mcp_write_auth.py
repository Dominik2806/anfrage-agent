"""Tests des Zugangsschutzes mit zwei Pfaden und zwei Token (F08, ADR 0005 Punkte 3 und 4).

Festlegungen:
- /mcp antwortet nur mit dem Lese-Token, /mcp-write nur mit dem Schreib-Token. Das jeweils falsche Token ergibt dieselbe
  401-Antwort wie ein fehlendes oder falsches Token (Inhalt und alle Kopfzeilen gleich, auch zwischen den Pfaden).
- Jeder Pfad braucht ein Token, auch die, die es nicht gibt (kein 404 ohne Token).
- Ohne Schreibkonfiguration gibt es keine Schreib-App und keine Weiche: /mcp-write ist ein unbekannter Pfad der
  Lese-App (401 ohne Token, 404 mit Lese-Token, 401 mit jedem anderen Token).
- Je Anfrage genau ein hmac.compare_digest mit Bytes, auf beiden Pfaden.

Ohne Datenbank. Rot, bis create_app mit Schreibkonfiguration die Weiche baut (TDD).
"""

import hmac
from collections.abc import Callable
from typing import Any

import pytest
from mcp_testkit import MCP_PATH, RPC_HEADERS, WRITE_PATH
from starlette.testclient import TestClient

TOOLS_LIST = {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
OTHER_PATHS = ["/mcp-write/", "/mcp-writer", "/MCP-WRITE", "//mcp-write"]


def _post(client: TestClient, path: str, headers: dict[str, str] | None = None) -> Any:
    return client.post(path, json=TOOLS_LIST, headers={**RPC_HEADERS, **(headers or {})})


def _post_with_header_list(client: TestClient, path: str, extra: list[tuple[str, str]]) -> Any:
    """Wie _post, aber mit einer Liste von Headern, damit derselbe Name mehrfach vorkommen kann."""
    return client.post(path, json=TOOLS_LIST, headers=[*RPC_HEADERS.items(), *extra])


def _bearer(value: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {value}"}


# Wer darf wohin


def test_the_write_path_needs_the_write_token(
    dual_client: TestClient, write_headers: dict[str, str], read_headers: dict[str, str]
) -> None:
    assert _post(dual_client, WRITE_PATH, write_headers).status_code == 200
    assert _post(dual_client, WRITE_PATH, read_headers).status_code == 401
    assert _post(dual_client, WRITE_PATH).status_code == 401
    assert _post(dual_client, WRITE_PATH, _bearer("z" * 43)).status_code == 401


def test_the_read_path_rejects_the_write_token(
    dual_client: TestClient, write_headers: dict[str, str], read_headers: dict[str, str]
) -> None:
    assert _post(dual_client, MCP_PATH, read_headers).status_code == 200
    assert _post(dual_client, MCP_PATH, write_headers).status_code == 401


def test_401_is_identical_in_both_directions_and_for_missing_or_wrong_tokens(
    dual_client: TestClient, write_headers: dict[str, str], read_headers: dict[str, str]
) -> None:
    """Die Antwort verrät weder, ob das Token fehlte oder falsch war, noch, dass es das des anderen Pfads ist."""
    wrong = _bearer("z" * 43)
    responses = {
        "lese-token-auf-schreib-pfad": _post(dual_client, WRITE_PATH, read_headers),
        "schreib-token-auf-lese-pfad": _post(dual_client, MCP_PATH, write_headers),
        "fehlt-lese-pfad": _post(dual_client, MCP_PATH),
        "fehlt-schreib-pfad": _post(dual_client, WRITE_PATH),
        "falsch-lese-pfad": _post(dual_client, MCP_PATH, wrong),
        "falsch-schreib-pfad": _post(dual_client, WRITE_PATH, wrong),
    }
    assert {name: r.status_code for name, r in responses.items()} == dict.fromkeys(responses, 401)
    reference = responses["fehlt-lese-pfad"]
    for name, response in responses.items():
        assert response.content == reference.content, name
        assert dict(response.headers) == dict(reference.headers), name
        assert response.headers["www-authenticate"] == "Bearer", name


def test_the_other_path_token_is_not_silently_forwarded(
    dual_client: TestClient, write_headers: dict[str, str], read_headers: dict[str, str]
) -> None:
    """Das Token des anderen Pfads ergibt 401 von der Middleware des gewählten Pfads, nie 404 oder 200."""
    for path, headers in ((WRITE_PATH, read_headers), (MCP_PATH, write_headers)):
        response = _post(dual_client, path, headers)
        assert response.status_code == 401, path
        assert "oauth" not in response.text.lower()


def test_both_tokens_at_once_do_not_help(
    dual_client: TestClient, write_headers: dict[str, str], read_headers: dict[str, str]
) -> None:
    both = [
        ("Authorization", read_headers["Authorization"]),
        ("Authorization", write_headers["Authorization"]),
    ]
    for path in (MCP_PATH, WRITE_PATH):
        assert _post_with_header_list(dual_client, path, both).status_code == 401
        assert _post_with_header_list(dual_client, path, both[::-1]).status_code == 401


# Falsche Formen auf dem Schreib-Pfad (wie test_mcp_auth.py für den Lese-Pfad)


@pytest.mark.parametrize("variant", ["prefix", "longer", "empty", "swapped-case"])
def test_near_miss_write_tokens_return_401(
    dual_client: TestClient, write_token: str, variant: str
) -> None:
    attempts = {
        "prefix": write_token[:-1],
        "longer": write_token + "x",
        "empty": "",
        "swapped-case": write_token.swapcase(),
    }
    assert _post(dual_client, WRITE_PATH, _bearer(attempts[variant])).status_code == 401


@pytest.mark.parametrize("scheme", ["Basic", "Token", "Digest"])
def test_other_schemes_return_401_on_the_write_path(
    dual_client: TestClient, write_token: str, scheme: str
) -> None:
    assert (
        _post(dual_client, WRITE_PATH, {"Authorization": f"{scheme} {write_token}"}).status_code
        == 401
    )


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
def test_correct_write_token_in_wrong_header_form_returns_401(
    dual_client: TestClient, write_token: str, form: str
) -> None:
    assert (
        _post(dual_client, WRITE_PATH, {"Authorization": form.format(t=write_token)}).status_code
        == 401
    )


def test_write_token_without_scheme_or_value_returns_401(
    dual_client: TestClient, write_token: str
) -> None:
    assert _post(dual_client, WRITE_PATH, {"Authorization": write_token}).status_code == 401
    assert _post(dual_client, WRITE_PATH, {"Authorization": "Bearer"}).status_code == 401
    assert _post(dual_client, WRITE_PATH, {"Authorization": "Bearer "}).status_code == 401


def test_write_token_in_query_parameter_is_not_accepted(
    dual_client: TestClient, write_token: str
) -> None:
    assert _post(dual_client, f"{WRITE_PATH}?token={write_token}").status_code == 401
    assert _post(dual_client, f"{WRITE_PATH}?access_token={write_token}").status_code == 401


def test_two_authorization_headers_return_401_on_the_write_path_even_if_one_is_correct(
    dual_client: TestClient, write_token: str
) -> None:
    good = ("Authorization", f"Bearer {write_token}")
    wrong = ("Authorization", "Bearer " + "q" * len(write_token))
    assert _post_with_header_list(dual_client, WRITE_PATH, [good, good]).status_code == 401
    assert _post_with_header_list(dual_client, WRITE_PATH, [good, wrong]).status_code == 401
    assert _post_with_header_list(dual_client, WRITE_PATH, [wrong, good]).status_code == 401


def test_websocket_connection_to_the_write_path_without_a_valid_token_fails(
    dual_client: TestClient, read_headers: dict[str, str]
) -> None:
    from starlette.websockets import WebSocketDisconnect

    for headers in ({}, read_headers, _bearer("z" * 43)):
        with pytest.raises(WebSocketDisconnect):
            with dual_client.websocket_connect(WRITE_PATH, headers=headers):
                pass


# Jeder Pfad braucht ein Token


@pytest.mark.parametrize(
    "path",
    [
        "/",
        "/foo",
        "/mcp/",
        "/mcp-write/",
        "/mcp-writer",
        "/MCP-WRITE",
        "//mcp-write",
        "/mcp-write?x=1",
        "/sse",
        "/health",
        "/.well-known/oauth-protected-resource",
        "/authorize",
    ],
)
def test_every_path_requires_a_token_with_the_write_path_configured(
    dual_client: TestClient, path: str
) -> None:
    """Keine Route ohne Token, auch nicht für Pfade, die es nicht gibt (kein 404 ohne Token)."""
    for method in ("GET", "POST", "DELETE"):
        response = dual_client.request(method, path)
        assert response.status_code == 401, f"{method} {path}"


@pytest.mark.parametrize("method", ["GET", "POST", "DELETE"])
def test_the_write_path_itself_requires_a_token_for_every_method(
    dual_client: TestClient, method: str
) -> None:
    assert dual_client.request(method, WRITE_PATH).status_code == 401


@pytest.mark.parametrize("path", OTHER_PATHS)
def test_paths_next_to_the_write_path_belong_to_the_read_app(
    dual_client: TestClient,
    path: str,
    read_headers: dict[str, str],
    write_headers: dict[str, str],
) -> None:
    """Nur der exakte Pfad gehört dem Schreib-Token. Daneben gilt das Lese-Token (dann kein Treffer: 404)."""
    assert _post(dual_client, path, write_headers).status_code == 401
    assert _post(dual_client, path, read_headers).status_code == 404


# Ohne Schreibkonfiguration


def test_without_write_config_the_write_path_is_an_unknown_path(
    client: TestClient, auth_headers: dict[str, str], write_token: str
) -> None:
    assert _post(client, WRITE_PATH).status_code == 401
    assert _post(client, WRITE_PATH, auth_headers).status_code == 404
    assert _post(client, WRITE_PATH, _bearer(write_token)).status_code == 401
    # Das Schreib-Token öffnet auch den Lese-Pfad nicht
    assert _post(client, MCP_PATH, _bearer(write_token)).status_code == 401


# Ein Vergleich je Anfrage


@pytest.mark.parametrize("path", [MCP_PATH, WRITE_PATH])
@pytest.mark.parametrize("case", ["fehlt", "falsch", "doppelt", "richtig"])
def test_each_request_makes_exactly_one_comparison_with_bytes(
    dual_client: TestClient,
    monkeypatch: pytest.MonkeyPatch,
    read_headers: dict[str, str],
    write_headers: dict[str, str],
    path: str,
    case: str,
) -> None:
    """Die Weiche vergleicht nichts; genau die Middleware des gewählten Pfads vergleicht, genau einmal."""
    from hoffmann_data import auth

    calls: list[tuple[Any, Any]] = []
    original: Callable[[Any, Any], bool] = hmac.compare_digest

    def spy(a: Any, b: Any) -> bool:
        calls.append((a, b))
        return original(a, b)

    monkeypatch.setattr(auth.hmac, "compare_digest", spy)
    own = write_headers if path == WRITE_PATH else read_headers
    if case == "fehlt":
        response = _post(dual_client, path)
    elif case == "falsch":
        response = _post(dual_client, path, _bearer("q" * 43))
    elif case == "doppelt":
        value = own["Authorization"]
        response = _post_with_header_list(
            dual_client, path, [("Authorization", value), ("Authorization", value)]
        )
    else:
        response = _post(dual_client, path, own)
    assert response.status_code == (200 if case == "richtig" else 401)
    assert len(calls) == 1, f"hmac.compare_digest wurde {len(calls)}-mal aufgerufen"
    assert all(isinstance(a, bytes) and isinstance(b, bytes) for a, b in calls)
