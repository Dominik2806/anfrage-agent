# Changelog

## \[Unveröffentlicht]

### Geändert

* Hooks gehärtet (Issue #4): git push mit globalen Optionen, gh pr merge, schreibende gh-api-Aufrufe,
Platzhalter und zusammengesetzte Namen für .env, Schreibzugriffe auf Hooks und Einstellungen
* Edit-Sperre für Hooks und Einstellungen, Rückfrage vor git push und Sperre für gh pr merge
* test-writer ohne Bash, Hook-Tests nach tests/hooks verschoben
* Hooks erkennen Hüllen und Unterausdrücke, Platzhalter und Windows-Namensformen für .env; Schreibzugriffe auf Hooks nur noch lesend erlaubt (Positivliste); überschreibende git-Befehle blockiert; Hook-Aufrufe fehlersicher
* F08: Die Schreibrolle `data_service_rw` liest `activities` nicht mehr (nur noch `INSERT`, keine `SELECT`-Policy darauf); die Startprüfung des Schreibwegs prüft Row Level Security (`rls-aus:<tabelle>`) und meldet ein `SELECT` auf `activities` als `lesen-unerlaubt:activities`. Für einen laufenden Betrieb: das Skript `db/roles/data_service_rw.sql` erneut ausführen (wiederholbar, es entzieht das alte `SELECT` und löscht die alte Policy `rw_select` auf `activities`); bis dahin bricht der Start mit `Schreibweg: lesen-unerlaubt:activities` ab. Das führt der Mensch aus.

### Hinzugefügt

* F01: Repository, Ordnerstruktur, CLAUDE.md, Entwicklungsprotokoll
* F02: Hooks für Claude Code (Schutz für .env-Dateien und main, automatische Formatierung) mit Testskript
* F03: Subagents (code-reviewer, security-reviewer, test-writer) und Skills (adr-schreiben, eval-fall-anlegen)
* F04: CI-Pipeline mit Hook-Tests, Formatierungsprüfung und Meldung geänderter Hooks, Tests und Pipeline; `.gitattributes` für einheitliche Zeilenenden; Pflichtprüfungen im Ruleset
* F05: Datenmodell und Schema mit Constraint-Tests; synthetische Testdaten (40 Artikel, 21 Kunden, 35 Kontakte, 77 Aktivitäten, 18 Rabattregeln, 24 Zuordnungen von Ersatzteil zu Anlage) und Richtlinien (Tonalität, Eskalation, Signatur, generierte rabatte.md); Befüllbefehl python -m db.seed mit Löschschutz (Bestätigung durch Hostnamen) und Konsistenzprüfungen; psycopg und pytest als Entwicklungsabhängigkeiten (requirements-dev.txt); der Job Schema-Tests führt tests/seed mit aus
* F06: MCP-Server-Grundgerüst „hoffmann-data“ (Python, Streamable HTTP, SDK mcp 2.3.0): Zugang nur mit Token (eigene Middleware, `Authorization: Bearer <token>`, jede Route ohne Token 401, WebSocket abgelehnt), Start nur mit gültigem Token, Host-/Origin-Schutz mit `MCP_SERVER_ALLOWED_HOSTS`, zustandsloser Transport; die acht Schnittstellen (fünf Tools, zwei Resources, ein Prompt) sind Platzhalter mit dem Fehler „noch nicht implementiert“; ADR 0003; neuer CI-Job MCP-Tests. Fortschrittsmeldungen bei länger laufenden Aufrufen gehören zu F10.
* F07: Lesewerkzeuge des Datenservice `search_products`, `get_product` und `find_customer` mit echten Daten aus PostgreSQL (nur lesend, Eingabeprüfung vor der Datenbank, feste Fehlermeldungen ohne Eingabewerte, keine internen IDs); Nur-Lese-Rolle `data_service_ro` (SQL-Skript `db/roles/data_service_ro.sql`, ohne Passwort) und Startprüfung der Rolle, die den Start bei zu weitgehenden Rechten abbricht und keinen Schalter hat; neue Variable `MCP_SERVER_DATABASE_URL`; Abhängigkeiten `psycopg[binary]` und `pydantic` (gepinnt); ADR 0004; der CI-Job MCP-Tests läuft gegen einen PostgreSQL-17-Dienst und überspringt keine Datenbanktests. `create_lead`, `log_activity`, beide Resources und der Prompt bleiben Platzhalter (F08 ff.).
* F08: Schreibwerkzeuge des Datenservice `create_lead` (Dublettenschutz über Domain, Firmenname und Adresse: ein Treffer über die Domain lässt den bestehenden Kunden unverändert und ergänzt nur fehlende Kontakte, ein Konflikt gibt eine feste Meldung und schreibt nichts, ein Wiederholungsversuch nach verlorenem Rennen, die Nachschlagungen in einer SQL-Anweisung) und `log_activity` (nur anlegen, nichts überschreiben oder löschen, keine internen IDs, Zeitstempel von der Datenbank); Schreibrolle `data_service_rw` (SQL-Skript `db/roles/data_service_rw.sql`, ohne Passwort) mit eigener Startprüfung ohne Schalter; neue Variablen `MCP_SERVER_WRITE_DATABASE_URL` und `MCP_SERVER_WRITE_TOKEN`; zwei Token und zwei Pfade (`/mcp` lesend, `/mcp-write` schreibend) mit Pfadweiche (`switch.py`); Eingabeprüfung der Schreibwerkzeuge in `limits.py`; ADR 0005. `create_lead` und `log_activity` lehnen E-Mail-Adressen mit Nicht-ASCII-Zeichen vor dem `@` ab (das `lower()` von Python und von PostgreSQL unterscheidet sich bei einigen Zeichen); `find_customer` ist nicht betroffen. Beide Resources und der Prompt bleiben Platzhalter (F09).

