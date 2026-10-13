# CLAUDE.md – mcp-server (Datenservice „hoffmann-data“)

Gilt zusätzlich zur CLAUDE.md im Hauptordner. Verbindlich: `docs/AUFTRAG.md` (Kapitel 5 und 6.4), `docs/adr/0003-token-pruefung-eigene-middleware.md`,
`docs/adr/0004-datenservice-liest-ueber-nur-lese-rolle.md`, `docs/adr/0005-schreibweg-des-datenservice.md`.

## Aufbau
- `hoffmann_data/config.py` – liest und prüft die Umgebung (`load_config`, `Config`, `ConfigError`), auch `MCP_SERVER_DATABASE_URL`
  und die beiden Schreibvariablen `MCP_SERVER_WRITE_DATABASE_URL` und `MCP_SERVER_WRITE_TOKEN`
- `hoffmann_data/dburl.py` – prüft die Datenbank-URL (gleiche Regeln wie das Seed-Skript)
- `hoffmann_data/auth.py` – ASGI-Middleware für das Zugangstoken (`BearerTokenMiddleware`)
- `hoffmann_data/switch.py` – Pfadweiche (`PathSwitch`): wählt allein nach dem exakten Pfad (`/mcp-write` Schreib-App, sonst Lese-App)
- `hoffmann_data/limits.py` – Eingabeprüfung der Lese- und Schreib-Werkzeuge mit festen Meldungen
- `hoffmann_data/db.py` – einziger Datenbankzugriff (`Database`, `read_connection`, `WriteDatabase`, `write_connection`,
  `fetch_all`), die einzige Stelle mit `INSERT INTO`, und die Startprüfungen der Rollen (`verify_read_only_role`,
  `verify_write_role`, `RoleCheckError`)
- `hoffmann_data/catalog.py` – `search_products`, `get_product`
- `hoffmann_data/crm.py` – `find_customer`, `log_activity`, `create_lead`
- `hoffmann_data/server.py` – die acht Schnittstellen: fünf Werkzeuge echt auf zwei Pfaden (`create_read_server` für `/mcp`,
  `create_write_server` für `/mcp-write`), beide Resources und der Prompt Platzhalter; Host-/Origin-Schutz, `create_app`
- `hoffmann_data/__main__.py` – Start (`python -m hoffmann_data` aus diesem Ordner), prüft vor `uvicorn.run` die Datenbankrolle
  (und, wenn konfiguriert, die Schreibrolle)
- `../db/roles/data_service_ro.sql` und `../db/roles/data_service_rw.sql` – Skripte der Nur-Lese- und der Schreibrolle (liegen
  außerhalb des Pakets, der Mensch führt sie aus)
- Tests: `tests/mcp_server/`

## Regeln
- Das Zugangstoken wird nie geloggt, gedruckt, in Fehlermeldungen oder Antworten ausgegeben, auch nicht
  gekürzt. Fehlermeldungen der Konfiguration nennen nur den Namen der Variablen. `Config.token` bleibt aus `repr`.
- Der Zugangsschutz läuft nur über die Middleware in `auth.py`, außen um die ganze App. Keine Route
  daneben anlegen, kein `custom_route` des SDKs, kein `AuthSettings`/`TokenVerifier`. Jede Route braucht das Token.
- Das Token wird nie gekürzt oder bereinigt. Ein ungültiges Token verhindert den Start.
- Keine Werkzeuge zum Versenden oder Löschen (Auftrag 5.1). Neue Werkzeuge nur nach Auftrag 6.4 und mit Test.
- Keine Scheindaten. Solange eine Schnittstelle nicht umgesetzt ist (heute beide Resources und der Prompt auf `/mcp`), wirft
  sie nur den festen Fehler „noch nicht implementiert“, ohne Eingabewerte in der Meldung.
- Prompts werfen `MCPError` (nicht eine einfache Ausnahme): Das SDK ersetzt dort sonst jede andere Ausnahme
  durch eine Allgemeinmeldung.
- Host-/Origin-Schutz bleibt immer ausdrücklich gesetzt. Hosts außer Loopback nur mit `MCP_SERVER_ALLOWED_HOSTS`.
- Transport: `stateless_http=True`, `json_response=False` (ADR 0003; F10 braucht Streaming).
- Kein Zugriffsprotokoll von uvicorn (`access_log=False`), weil es Pfad und Query schreiben würde.
- Verhalten des SDKs `mcp` nicht aus einer Stelle im Quelltext folgern, sondern mit einem Test belegen.
- Tests: Die ASGI-App läuft im Speicher (Starlette-`TestClient` mit `base_url="http://127.0.0.1:8000"`), es wird kein Port
  geöffnet. Tests zu Token, Konfiguration und Schnittstellen brauchen keine Datenbank. Tests mit Datenbank brauchen
  `TEST_DATABASE_URL` (nur `localhost:5432`); ohne sie werden sie lokal übersprungen, in GitHub Actions ist das ein Fehler.
  Tests der Schreibwerkzeuge committen Zeilen mit dem Marker `mcp-schreibtest` und räumen vor und nach jedem Test auf; im
  Testschema ist Autovacuum abgeschaltet (Sperrzyklus mit dem `ro`-Skript, AGENT_LOG, 2026-10-12).
- Nur `db.py` importiert `psycopg` (geprüft per AST von `test_mcp_no_leak.py`). `DATABASE_URL` (die Variable des Seed-Skripts)
  bleibt im Paket verboten, die Variable des Datenservice heißt `MCP_SERVER_DATABASE_URL`.
- Datenbank (ADR 0004 und 0005): Der Lesepfad liest nur, als Rolle `data_service_ro`. Der Schreibpfad legt nur an, als Rolle
  `data_service_rw` (`SELECT` und `INSERT` auf `customers`, `contacts`, nur `INSERT` auf `activities`), und nur wenn `MCP_SERVER_WRITE_DATABASE_URL`
  und `MCP_SERVER_WRITE_TOKEN` beide gesetzt sind. `main()` prüft beide Rollen vor dem Start (`db.verify_read_only_role`,
  `db.verify_write_role`, Aufruf über das Modul `db`). Es gibt dafür keinen Schalter und keine zusätzliche Umgebungsvariable.
- SQL steht nur als feste Konstanten mit benannten Parametern (Text als `::text`), nie per f-String, `%`, `+` oder `.format`.
  Rechtenamen (`INSERT`, `SELECT` und so weiter) gehen nur als Parameter an die Abfrage, nie in den SQL-Text.
  `INSERT INTO` steht nur in `db.py`, jede Anweisung in einer eigenen Zeichenkette; es gibt kein `UPDATE` und kein `DELETE`.
  `test_package_sql_rules.py` prüft das.
- Schreibweg (ADR 0005): Zwei Token, zwei Pfade. Das Schreib-Token hat nur der Freigabeweg, nie der Agent. Die Weiche wählt nur
  nach dem Pfad und prüft kein Token.
- **Nachschlagungen, deren Ergebnisse gemeinsam über einen Konflikt entscheiden, laufen in einer SQL-Anweisung**
  (`db.find_lead_matches`): Unter READ COMMITTED sieht jede Anweisung einen eigenen Stand, getrennte Abfragen erzeugen sonst einen
  falschen Konflikt (AGENT_LOG, 2026-10-12).
- Wiederholung nach verlorenem Rennen (`create_lead`): nur bei `DUPLICATE_ERROR`, genau einmal, der ganze Ablauf in einer neuen
  Transaktion. Andere Fehler werden nicht wiederholt.
- Fehlermeldungen sind fest und enthalten nie Eingabewerte, Rollenname, Host, Benutzer, Datenbankname, URL oder Passwort.
  Bekannte Ausnahme (ADR 0004, Issue #21): Fehlt ein Pflichtargument, gibt das SDK den Argument-Dict (bis etwa 50 Zeichen) zurück.
  Das Log nennt bei Datenbankfehlern nur Klasse und SQLSTATE.
- Eingaben prüft `limits.py`, bevor die Datenbank berührt wird. Textparameter der Werkzeuge sind
  `Annotated[Any, WithJsonSchema(...)]` mit eigener Prüfung, weil das SDK bei Pydantic-Fehlern den Eingabewert zurückgibt.
- Die Regeltests (`test_package_sql_rules.py`, `test_mcp_no_leak.py`, `test_role_check.py`, `test_limits.py`) ändert nur der Mensch.
  Claude Code ändert sie nur nach ausdrücklicher Freigabe.
- Sonderzeichen in Tests nur als achtstellige `\U`-Escapes, nie wörtlich (ruff PLE2502, PLE2515).
- Neue Abhängigkeiten nur nach Rückfrage. `mcp`, `pydantic` und `psycopg` sind fest gepinnt (`requirements.txt`).
