"""Zugangsschutz: reine ASGI-Middleware mit festem Bearer-Token (F06, ADR 0003).

Akzeptiert wird nur der Header "Authorization: Bearer <token>" genau in dieser Form. Der Vergleich
läuft mit hmac.compare_digest auf Bytes, auch wenn der Header fehlt. Jeder Fehlerfall liefert dieselbe
401-Antwort mit festem Text. Weder Token noch Eingaben stehen in einer Antwort oder einem Log.
Bewusst kein BaseHTTPMiddleware: Es kann Ereignisströme (SSE) stören.
"""

import hmac

from starlette.types import ASGIApp, Message, Receive, Scope, Send

_BODY = "Zugriff verweigert: ein gültiges Zugangstoken ist erforderlich.".encode()
_UNAUTHORIZED_HEADERS = [
    (b"content-type", b"text/plain; charset=utf-8"),
    (b"content-length", str(len(_BODY)).encode()),
    (b"www-authenticate", b"Bearer"),
    (b"cache-control", b"no-store"),
]


class BearerTokenMiddleware:
    def __init__(self, app: ASGIApp, token: str) -> None:
        self._app = app
        self._expected = b"Bearer " + token.encode("ascii")

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        scope_type = scope["type"]
        if scope_type == "lifespan":
            await self._app(scope, receive, send)
        elif scope_type == "http":
            if self._is_authorized(scope):
                await self._app(scope, receive, send)
            else:
                await self._reject_http(send)
        elif scope_type == "websocket":
            await self._reject_websocket(receive, send)
        # Unbekannte Scope-Typen werden nicht an die App weitergegeben.

    def _is_authorized(self, scope: Scope) -> bool:
        values = [value for name, value in scope["headers"] if name.lower() == b"authorization"]
        # Mehrere Authorization-Header sind nie gültig. Verglichen wird trotzdem immer genau einmal.
        given = values[0] if len(values) == 1 else b""
        matches = hmac.compare_digest(given, self._expected)
        return matches and len(values) == 1

    @staticmethod
    async def _reject_http(send: Send) -> None:
        start: Message = {
            "type": "http.response.start",
            "status": 401,
            "headers": _UNAUTHORIZED_HEADERS,
        }
        await send(start)
        await send({"type": "http.response.body", "body": _BODY})

    @staticmethod
    async def _reject_websocket(receive: Receive, send: Send) -> None:
        await receive()  # websocket.connect
        await send({"type": "websocket.close", "code": 1008})
