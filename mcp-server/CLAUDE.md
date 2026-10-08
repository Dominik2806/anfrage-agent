# CLAUDE.md – mcp-server (Datenservice „hoffmann-data“)

Gilt zusätzlich zur CLAUDE.md im Hauptordner. Verbindlich: `docs/AUFTRAG.md` (Kapitel 5 und 6.4), `docs/adr/0003-token-pruefung-eigene-middleware.md`.

## Aufbau
- `hoffmann_data/config.py` – liest und prüft die Umgebung (`load_config`, `Config`, `ConfigError`)
- `hoffmann_data/auth.py` – ASGI-Middleware für das Zugangstoken (`BearerTokenMiddleware`)
- `hoffmann_data/server.py` – die acht Schnittstellen, Host-/Origin-Schutz, `create_app`
- `hoffmann_data/__main__.py` – Start (`python -m hoffmann_data` aus diesem Ordner)
- Tests: `tests/mcp_server/`

## Regeln
- Das Zugangstoken wird nie geloggt, gedruckt, in Fehlermeldungen oder Antworten ausgegeben, auch nicht
  gekürzt. Fehlermeldungen der Konfiguration nennen nur den Namen der Variablen. `Config.token` bleibt aus `repr`.
- Der Zugangsschutz läuft nur über die Middleware in `auth.py`, außen um die ganze App. Keine Route
  daneben anlegen, kein `custom_route` des SDKs, kein `AuthSettings`/`TokenVerifier`. Jede Route braucht das Token.
- Das Token wird nie gekürzt oder bereinigt. Ein ungültiges Token verhindert den Start.
- Keine Werkzeuge zum Versenden oder Löschen (Auftrag 5.1). Neue Werkzeuge nur nach Auftrag 6.4 und mit Test.
- Keine Scheindaten. Solange eine Schnittstelle nicht umgesetzt ist, wirft sie nur den festen Fehler
  „noch nicht implementiert“, ohne Eingabewerte in der Meldung.
- Prompts werfen `MCPError` (nicht eine einfache Ausnahme): Das SDK ersetzt dort sonst jede andere Ausnahme
  durch eine Allgemeinmeldung.
- Host-/Origin-Schutz bleibt immer ausdrücklich gesetzt. Hosts außer Loopback nur mit `MCP_SERVER_ALLOWED_HOSTS`.
- Transport: `stateless_http=True`, `json_response=False` (ADR 0003; F10 braucht Streaming).
- Kein Zugriffsprotokoll von uvicorn (`access_log=False`), weil es Pfad und Query schreiben würde.
- Verhalten des SDKs `mcp` nicht aus einer Stelle im Quelltext folgern, sondern mit einem Test belegen.
- Tests laufen ohne Datenbank und ohne Netzwerk: ASGI-App im Speicher (Starlette-`TestClient` mit
  `base_url="http://127.0.0.1:8000"`), kein Port, kein Zugriff nach außen. Im Paket kein `psycopg`, kein `DATABASE_URL`.
- Neue Abhängigkeiten nur nach Rückfrage. `mcp` ist fest gepinnt (`requirements.txt`).
