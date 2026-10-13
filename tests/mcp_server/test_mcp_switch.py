"""Tests der Pfadweiche hoffmann_data/switch.py (F08, ADR 0005 Punkt 3).

Schnittstelle, die diese Tests festlegen:
- PathSwitch(read_app, write_app, *, write_path, lifespans) ist eine reine ASGI-App.
- Für Scopes vom Typ http und websocket wählt sie allein nach dem exakten Pfad: scope["path"] == write_path geht an
  write_app, alles andere (auch /mcp-write/, /MCP-WRITE, /mcp-writer, //mcp-write) an read_app. Der Query-String
  gehört nicht zum Pfad. Die Weiche liest weder Body noch Kopfzeilen, prüft kein Token, ändert den Scope nicht und
  gibt scope, receive und send unverändert weiter. Sie antwortet nie selbst.
- Andere Scope-Typen (außer lifespan, siehe test_mcp_switch_lifespan.py) werden an keine App weitergegeben, ohne Fehler.
- Die Weiche hat keinen Zustand zwischen Anfragen.

Ohne SDK: Die beiden Apps sind Fakes (mcp_testkit.RecordingApp). Rot, bis hoffmann_data/switch.py existiert (TDD).
"""

import asyncio
import copy
from typing import Any

import pytest
from mcp_testkit import WRITE_PATH, RecordingApp


def _switch(read: Any, write: Any) -> Any:
    from hoffmann_data.switch import PathSwitch

    return PathSwitch(read, write, write_path=WRITE_PATH, lifespans=())


def _scope(path: str, scope_type: str = "http", **extra: Any) -> dict[str, Any]:
    scope: dict[str, Any] = {
        "type": scope_type,
        "path": path,
        "query_string": b"",
        "headers": [],
        "method": "POST",
    }
    scope.update(extra)
    return scope


class Channel:
    """receive darf von der Weiche nie aufgerufen werden; send schreibt mit."""

    def __init__(self) -> None:
        self.received = 0
        self.sent: list[dict[str, Any]] = []

    async def receive(self) -> dict[str, Any]:
        self.received += 1
        raise AssertionError("die Weiche darf receive nicht aufrufen (kein Body)")

    async def send(self, message: dict[str, Any]) -> None:
        self.sent.append(message)


def _dispatch(switch: Any, scope: dict[str, Any]) -> Channel:
    channel = Channel()
    asyncio.run(switch(scope, channel.receive, channel.send))
    return channel


def _body(channel: Channel) -> bytes:
    return b"".join(m.get("body", b"") for m in channel.sent if m["type"] == "http.response.body")


def test_exact_write_path_goes_to_the_write_app() -> None:
    read, write = RecordingApp("lese"), RecordingApp("schreib")
    channel = _dispatch(_switch(read, write), _scope(WRITE_PATH))
    assert len(write.calls) == 1 and read.calls == []
    assert _body(channel) == b"schreib"


@pytest.mark.parametrize(
    "path",
    [
        pytest.param("/mcp", id="lese-pfad"),
        pytest.param("/", id="wurzel"),
        pytest.param("", id="leer"),
        pytest.param("/mcp-write/", id="schrägstrich-hinten"),
        pytest.param("/MCP-WRITE", id="grossbuchstaben"),
        pytest.param("/Mcp-Write", id="gemischt"),
        pytest.param("/mcp-writer", id="längerer-name"),
        pytest.param("/mcp-write2", id="mit-ziffer"),
        pytest.param("//mcp-write", id="doppelter-schrägstrich-vorn"),
        pytest.param("/mcp-write/x", id="unterpfad"),
        pytest.param("/x/mcp-write", id="oberpfad"),
        pytest.param("/mcp-wri", id="kürzer"),
        pytest.param("mcp-write", id="ohne-schrägstrich"),
        pytest.param("/mcp-write ", id="leerzeichen-hinten"),
        pytest.param("/mcp-write\x00", id="nul-hinten"),
        pytest.param("/mcp%2Dwrite", id="prozent-kodiert-wörtlich"),
    ],
)
def test_every_other_path_goes_to_the_read_app(path: str) -> None:
    read, write = RecordingApp("lese"), RecordingApp("schreib")
    channel = _dispatch(_switch(read, write), _scope(path))
    assert len(read.calls) == 1 and write.calls == []
    assert _body(channel) == b"lese"


def test_the_query_string_does_not_change_the_choice() -> None:
    read, write = RecordingApp("lese"), RecordingApp("schreib")
    switch = _switch(read, write)
    _dispatch(switch, _scope(WRITE_PATH, query_string=b"x=/mcp"))
    _dispatch(switch, _scope("/mcp", query_string=b"p=/mcp-write"))
    _dispatch(switch, _scope("/mcp", query_string=b"/mcp-write"))
    assert [s["path"] for s in write.scopes] == [WRITE_PATH]
    assert [s["path"] for s in read.scopes] == ["/mcp", "/mcp"]


@pytest.mark.parametrize(
    ("path", "expected"), [(WRITE_PATH, "schreib"), ("/mcp", "lese"), ("/x", "lese")]
)
def test_websocket_scopes_follow_the_same_rule(path: str, expected: str) -> None:
    read, write = RecordingApp("lese"), RecordingApp("schreib")
    _dispatch(_switch(read, write), _scope(path, "websocket"))
    called = write if expected == "schreib" else read
    other = read if expected == "schreib" else write
    assert len(called.calls) == 1 and other.calls == []


def test_the_switch_never_reads_the_body() -> None:
    read, write = RecordingApp("lese"), RecordingApp("schreib")
    switch = _switch(read, write)
    channels = [_dispatch(switch, _scope(WRITE_PATH)), _dispatch(switch, _scope("/mcp"))]
    assert [c.received for c in channels] == [0, 0]


def test_the_switch_does_not_look_at_headers_or_tokens() -> None:
    """Ohne Schlüssel `headers` im Scope, mit beliebigem Authorization-Header und ohne Token: gleich geroutet.

    Die Weiche antwortet nie selbst 401; das tut die Middleware der gewählten App.
    """
    read, write = RecordingApp("lese"), RecordingApp("schreib")
    switch = _switch(read, write)
    without_headers = _scope(WRITE_PATH)
    del without_headers["headers"]
    _dispatch(switch, without_headers)
    _dispatch(switch, _scope(WRITE_PATH, headers=[(b"authorization", b"Bearer irgendwas")]))
    _dispatch(switch, _scope("/mcp", headers=[(b"authorization", b"Bearer " + b"x" * 43)]))
    _dispatch(switch, _scope("/mcp", headers=[(b"authorization", b"a"), (b"authorization", b"b")]))
    assert len(write.calls) == 2 and len(read.calls) == 2


def test_the_switch_passes_scope_receive_and_send_through_unchanged() -> None:
    read, write = RecordingApp("lese"), RecordingApp("schreib")
    switch = _switch(read, write)
    for path, app in ((WRITE_PATH, write), ("/mcp", read)):
        scope = _scope(path, headers=[(b"host", b"127.0.0.1:8000")], custom={"a": [1, 2]})
        before = copy.deepcopy(scope)
        channel = Channel()
        asyncio.run(switch(scope, channel.receive, channel.send))
        ((seen_scope, seen_receive, seen_send),) = app.calls
        assert seen_scope is scope, "derselbe Scope, keine Kopie"
        assert seen_receive == channel.receive and seen_send == channel.send
        assert scope == before, "der Scope bleibt unverändert"


def test_unknown_scope_types_are_not_forwarded() -> None:
    read, write = RecordingApp("lese"), RecordingApp("schreib")
    switch = _switch(read, write)
    for scope_type in ("foo", "http.extension", ""):
        channel = _dispatch(switch, _scope(WRITE_PATH, scope_type))
        assert channel.sent == [] and channel.received == 0
    assert read.calls == [] and write.calls == []


def test_the_switch_answers_no_request_itself() -> None:
    """Schweigt die App, schweigt die Weiche: gesendet wird nur, was die gewählte App sendet."""

    class SilentApp:
        def __init__(self) -> None:
            self.calls = 0

        async def __call__(self, scope: Any, receive: Any, send: Any) -> None:
            self.calls += 1

    read, write = SilentApp(), SilentApp()
    switch = _switch(read, write)
    for path in (WRITE_PATH, "/mcp", "/unbekannt", "/mcp-write/"):
        assert _dispatch(switch, _scope(path)).sent == []
    assert (read.calls, write.calls) == (3, 1)


def test_the_choice_is_made_per_request_without_state() -> None:
    read, write = RecordingApp("lese"), RecordingApp("schreib")
    switch = _switch(read, write)
    sequence = [WRITE_PATH, "/mcp", "/mcp", WRITE_PATH, WRITE_PATH, "/mcp-write/", "/mcp"]
    bodies = [_body(_dispatch(switch, _scope(path))) for path in sequence]
    assert bodies == [b"schreib", b"lese", b"lese", b"schreib", b"schreib", b"lese", b"lese"]


def test_two_switches_share_no_state() -> None:
    read_a, write_a = RecordingApp("lese-a"), RecordingApp("schreib-a")
    read_b, write_b = RecordingApp("lese-b"), RecordingApp("schreib-b")
    switch_a, switch_b = _switch(read_a, write_a), _switch(read_b, write_b)
    _dispatch(switch_a, _scope(WRITE_PATH))
    _dispatch(switch_b, _scope("/mcp"))
    assert len(write_a.calls) == 1 and read_a.calls == []
    assert len(read_b.calls) == 1 and write_b.calls == []


def test_the_write_path_of_the_switch_is_a_parameter() -> None:
    """Der Pfad steht nicht fest in der Weiche: Mit einem anderen Pfad gilt die gleiche exakte Regel."""
    from hoffmann_data.switch import PathSwitch

    read, write = RecordingApp("lese"), RecordingApp("schreib")
    switch = PathSwitch(read, write, write_path="/anders", lifespans=())
    _dispatch(switch, _scope("/anders"))
    _dispatch(switch, _scope(WRITE_PATH))
    assert [s["path"] for s in write.scopes] == ["/anders"]
    assert [s["path"] for s in read.scopes] == [WRITE_PATH]
