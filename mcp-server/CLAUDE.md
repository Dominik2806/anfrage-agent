# CLAUDE.md – mcp-server (Datenservice „hoffmann-data“)

Gilt zusätzlich zur CLAUDE.md im Hauptordner. Verbindlich: `docs/AUFTRAG.md` (Kapitel 5 und 6.4), `docs/adr/0003-token-pruefung-eigene-middleware.md`,
`docs/adr/0004-datenservice-liest-ueber-nur-lese-rolle.md`, `docs/adr/0005-schreibweg-des-datenservice.md`,
`docs/adr/0006-richtlinien-und-prompt-aus-dateien.md`, `docs/adr/0007-fortschrittsmeldungen.md`.

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
- `hoffmann_data/crm.py` – `find_customer`, `log_activity`, `create_lead` (je mit optionalem `progress`, Standard `NO_PROGRESS`)
- `hoffmann_data/progress.py` – Fortschrittsmeldungen (F10, ADR 0007): `Step` (feste Texte), `ProgressReporter`, `NoProgress`/`NO_PROGRESS`,
  `PROGRESS_TIMEOUT_SECONDS`, `MAX_MESSAGES`, `progress_for` (begrenzt den `progressToken`, `MAX_TOKEN_LENGTH`, `MAX_TOKEN_INT`); kennt keine Datenbank
- `hoffmann_data/policies.py` – liest die Richtlinien aus `data/richtlinien/` (`POLICY_DIR`, je Abruf, feste Meldung
  `POLICY_UNAVAILABLE`) und prüft sie beim Start (`verify_policies`)
- `hoffmann_data/draft.py` – die beiden Nachrichten des Prompts als reine Funktion (`build_messages`), ohne SDK und ohne Dateizugriff
- `hoffmann_data/draft_prompt.py` – `DraftPrompt`, Unterklasse von `Prompt` des SDK mit eigenem `render`
- `hoffmann_data/server.py` – die acht Schnittstellen: fünf Werkzeuge auf zwei Pfaden (`create_read_server` für `/mcp`,
  `create_write_server` für `/mcp-write`), beide Resources und der Prompt auf `/mcp`; Host-/Origin-Schutz, `create_app`
- `hoffmann_data/__main__.py` – Start (`python -m hoffmann_data` aus diesem Ordner), prüft vor `uvicorn.run` die Datenbankrolle
  (und, wenn konfiguriert, die Schreibrolle) und danach die Richtlinien
- `../db/roles/data_service_ro.sql` und `../db/roles/data_service_rw.sql` – Skripte der Nur-Lese- und der Schreibrolle (liegen
  außerhalb des Pakets, der Mensch führt sie aus)
- Tests: `tests/mcp_server/`. Tests der Fortschrittsmeldungen (`test_progress_*.py`): `from_thread`, `eras`, `backpressure`, `cancellation` und
  `sdk_client` belegen Verhalten des SDKs mit einem eigenen Werkzeug des Tests (ohne Paketcode); `unit`, `tools`, `stream`, `security` und `server`
  prüfen den Datenservice. Hilfen (`ScriptedDatabase`, `AsgiRunner`, `serve_on_loopback`, `probe_app`) stehen in `mcp_testkit.py`.

## Regeln
- Das Zugangstoken wird nie geloggt, gedruckt, in Fehlermeldungen oder Antworten ausgegeben, auch nicht
  gekürzt. Fehlermeldungen der Konfiguration nennen nur den Namen der Variablen. `Config.token` bleibt aus `repr`.
- Der Zugangsschutz läuft nur über die Middleware in `auth.py`, außen um die ganze App. Keine Route
  daneben anlegen, kein `custom_route` des SDKs, kein `AuthSettings`/`TokenVerifier`. Jede Route braucht das Token.
- Das Token wird nie gekürzt oder bereinigt. Ein ungültiges Token verhindert den Start.
- Keine Werkzeuge zum Versenden oder Löschen (Auftrag 5.1). Neue Werkzeuge nur nach Auftrag 6.4 und mit Test.
- Keine Scheindaten. Eine Schnittstelle, die noch nicht umgesetzt ist, wirft nur einen festen Fehler „noch nicht implementiert“,
  ohne Eingabewerte in der Meldung (heute sind alle acht umgesetzt).
- Richtlinien (ADR 0006): Resources und Prompt lesen nur `data/richtlinien/`, je Abruf, über `policies.POLICY_DIR`. Es gibt keine
  Umgebungsvariable dafür (`test_role_check.py` zählt die `MCP_`-Namen); Tests ersetzen den Ordner mit dem Parameter `policy_dir`
  oder `monkeypatch`. Jeder Lesefehler ist die eine feste Meldung, ohne Pfad, Dateinamen und Ausnahmetext. `rabatte.md` und
  `eskalation.md` stehen nie im Entwurfsprompt, Kundentext nie in Nachricht 1. `read_policy` lehnt einen Symlink ab, dessen Ziel
  außerhalb des Ordners liegt (einer innerhalb ist erlaubt) und öffnet den aufgelösten Pfad; eine BOM wird akzeptiert (`utf-8-sig`),
  eine Datei nur aus BOM ist leer. Ein doppelter `##`-Abschnitt in `signatur.md` ist ein Fehler (`richtlinie-abschnitt:signatur`),
  Abschnitte werden nie zusammengeführt. Die zwei Symlink-Tests brauchen unter Windows den Entwicklermodus, sonst werden sie
  übersprungen. `policy://rabatte` gehört nicht in den Kontext, in dem der Entwurf für den Kunden entsteht (ADR 0006, Notiz für F14).
- Prompts: eigene Unterklasse von `Prompt` (`DraftPrompt`), registriert mit `server.add_prompt`; `render` prüft die Argumente selbst
  und wirft nur `MCPError` (nicht eine einfache Ausnahme): Das SDK macht aus jeder anderen Ausnahme einen Fehler mit Code 0 und
  loggt ihn samt Ursachenkette, bei einem Pydantic-Fehler mit dem Eingabewert. Der Prompt hängt an `mcp==2.3.0` (`Prompt.render`,
  `add_prompt`); bei einem Update von `mcp` zuerst `tests/mcp_server` laufen lassen.
- Rahmen im Prompt (`draft.py`): Es gibt sieben Marken je `n` (sechs Feldmarken und `<<<ENDE-DATEN-n>>>`); die Abschlussmarke ist ein
  Teil von `_marks` und damit von `_free_number`. Eine Marke, die nicht von `n` abhängt, ließe `_free_number` endlos laufen. Nachricht 1
  und die erste Zeile von Nachricht 2 nennen `n`; die Regelsätze (`RULE_FIRST_LAST`, `RULE_LOOKALIKE`, `RULE_CUSTOMER_BLOCK`) und
  `RULE_DELIVERY` (ohne Einheit, weil `tonalitaet.md` die Einheit festlegt) stehen als Konstanten in `draft.py` und sind durch
  `test_mcp_prompt.py` festgelegt.
- Optionale Argumente des Prompts (`kunde`, `belegte_angaben`): fehlt der Wert oder ist er nach `strip` leer, gilt er als „keine
  Angabe“ (`limits.check_optional_free_text`); ein Wert nur aus Steuerzeichen bleibt ungültig, `sprache` und `anfrage` leer ebenso.
- Texte im Code (Zeichenketten, nicht Docstrings) enthalten keines der Schlüsselwörter der SQL-Regel (`INSERT`, `UPDATE`, `DELETE`,
  `DROP`, `ALTER`, `TRUNCATE`, `GRANT`, `COPY`, `CREATE`, `MERGE`, auch nicht als deutsches Wort „Alter“) und keinen String mit
  Präfix `MCP_`; das gilt auch für Vorspann, Prompt-Anweisungen und Fehlermeldungen (`test_package_sql_rules.py`).
- DEBUG nie mit echten Daten: Bei DEBUG schreibt der Transport die ganze Antwort ins Log (`sse_starlette.sse`, „chunk:“), bei
  `prompts/get` samt Kundentext, bei `policy://rabatte` samt Prozentwerten, bei Fortschrittsmeldungen samt `progressToken`. Unser Code loggt keine Werte.
- Host-/Origin-Schutz bleibt immer ausdrücklich gesetzt. Hosts außer Loopback nur mit `MCP_SERVER_ALLOWED_HOSTS`.
- Transport: `stateless_http=True`, `json_response=False` (ADR 0003; F10 nutzt den Ereignisstrom, ADR 0007).
- Fortschrittsmeldungen (F10, ADR 0007): Meldungen sind nur die Texte von `Step`, der Reporter nimmt nur einen `Step`, nie einen Text oder Wert.
  Neue Texte nur in `Step`, ohne Schlüsselwörter der Paketregel und ohne `MCP_`. Die Schreibwerkzeuge melden nie innerhalb der Transaktion (vor dem
  Öffnen der Verbindung, nach dem Commit, bei der Wiederholung nach dem Rollback); `find_customer` meldet innerhalb der lesenden Verbindung.
  Best-Effort: Ein Sendefehler bricht nie ab und der Reporter schaltet sich danach ab; im Log nur die Klasse der Ausnahme. Zeitlimit je Meldung
  `PROGRESS_TIMEOUT_SECONDS`, höchstens `MAX_MESSAGES` Meldungen je Aufruf. `progress.py` importiert keine Datenbank. Keine neue Umgebungsvariable.
  `asyncio.CancelledError` kommt im Worker-Thread an und wird deshalb gefangen (Beleg `test_progress_cancellation.py`). Der `progressToken` des Clients
  steht nur in den Meldungen (bewusste Ausnahme von „keine Eingabewerte in Antworten“), nie im Log unseres Codes (ab INFO), nie in stdout oder stderr. Den Reporter baut nur `progress_for(ctx, total)`: Es meldet nur bei einer
  Zeichenkette bis `MAX_TOKEN_LENGTH` Zeichen (Codepunkte, nicht Bytes; auch leer) oder einer Ganzzahl bis `MAX_TOKEN_INT` im Betrag. Geprüft wird der Wert, den das SDK zurückschickt: `progress_token_from_params` auf
  `ctx.request_context.params` (nie `ctx.request_context.meta`: ein zusätzlicher Schlüssel `progress_token` neben `progressToken` im `_meta` überschreibt dort den Wert); alles
  andere ist `NO_PROGRESS`, still, ohne Log-Zeile und ohne Fehlermeldung. Neue Werkzeuge mit Fortschritt benutzen `progress_for`, nie `ProgressReporter` direkt. `anyio` ist
  direkt importiert und deshalb gepinnt.
- Kein Zugriffsprotokoll von uvicorn (`access_log=False`), weil es Pfad und Query schreiben würde.
- Verhalten des SDKs `mcp` nicht aus einer Stelle im Quelltext folgern, sondern mit einem Test belegen.
- Tests: Die ASGI-App läuft im Speicher (Starlette-`TestClient` mit `base_url="http://127.0.0.1:8000"` oder direkt als ASGI mit
  `AsgiRunner`), es wird kein Port geöffnet. **Ausnahme (ADR 0007):** `test_progress_server.py` und `test_progress_sdk_client.py` starten uvicorn
  im Thread über `serve_on_loopback` (nur `127.0.0.1`, Port 0, Socket vorab gebunden, `SelectorEventLoop`, ereignisbasiert, harte Zeitlimits);
  ohne Sockets Skip lokal, in GitHub Actions ein Fehler (`serve_or_skip`). Weitere Port-Tests nur mit dieser Hilfe und nur, wenn der Beweis ohne Port
  nicht reicht. Tests zu Token, Konfiguration und Schnittstellen brauchen keine Datenbank. Tests mit Datenbank brauchen
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
  Zweite bekannte Ausnahme (ADR 0006, Issue #37, gleiche Klasse wie #21): Bei einer unbekannten Resource gibt das SDK die URI zurück (Meldung, `data.uri`, INFO-Log),
  bei einem unbekannten Prompt den Namen (Meldung, ERROR-Log mit Stacktrace); beides als `xfail(strict=True)` in
  `test_mcp_policy_no_leak.py`. Auf `/mcp-write` (keine Resources, keine Prompts) läuft ein solcher Aufruf durch dieselbe
  SDK-Behandlung; die Tests dort prüfen nur den Fehler, nicht ein Echo des Werts.
  Dritte bekannte Klasse (schon vor F10, nur authentifizierte Clients, keine Verstärkung durch Wiederholung): Client-Text, den das SDK in Antworten oder Logs übernimmt.
  Selbst gelesen: `UNSUPPORTED_PROTOCOL_VERSION` spiegelt den Versionswert des Clients in `data` (`mcp/shared/inbound.py`, `unsupported_protocol_version_rejection`).
  Laut Review (F10), nicht selbst geprüft: `Parse error` und `Validation error` mit Pydantic-Text auf dem alten Weg, und ein INFO-Log `Tool %r failed` mit dem Werkzeugnamen des
  Clients. Nicht durch einen Test festgehalten (Eintrag im Sammel-Issue).
  Das Log nennt bei Datenbankfehlern nur Klasse und SQLSTATE.
- Eingaben prüft `limits.py`, bevor die Datenbank berührt wird. Textparameter der Werkzeuge sind
  `Annotated[Any, WithJsonSchema(...)]` mit eigener Prüfung, weil das SDK bei Pydantic-Fehlern den Eingabewert zurückgibt.
- Die Regeltests (`test_package_sql_rules.py`, `test_mcp_no_leak.py`, `test_role_check.py`, `test_limits.py`) ändert nur der Mensch.
  Claude Code ändert sie nur nach ausdrücklicher Freigabe.
- Sonderzeichen in Tests nur als achtstellige `\U`-Escapes, nie wörtlich (ruff PLE2502, PLE2515).
- Neue Abhängigkeiten nur nach Rückfrage. `mcp`, `pydantic`, `psycopg` und `anyio` (direkt importiert, F10) sind fest gepinnt (`requirements.txt`).
