# Changelog

## \[Unveröffentlicht]

### Geändert

* Hooks gehärtet (Issue #4): git push mit globalen Optionen, gh pr merge, schreibende gh-api-Aufrufe,
Platzhalter und zusammengesetzte Namen für .env, Schreibzugriffe auf Hooks und Einstellungen
* Edit-Sperre für Hooks und Einstellungen, Rückfrage vor git push und Sperre für gh pr merge
* test-writer ohne Bash, Hook-Tests nach tests/hooks verschoben
* Hooks erkennen Hüllen und Unterausdrücke, Platzhalter und Windows-Namensformen für .env; Schreibzugriffe auf Hooks nur noch lesend erlaubt (Positivliste); überschreibende git-Befehle blockiert; Hook-Aufrufe fehlersicher

### Hinzugefügt

* F01: Repository, Ordnerstruktur, CLAUDE.md, Entwicklungsprotokoll
* F02: Hooks für Claude Code (Schutz für .env-Dateien und main, automatische Formatierung) mit Testskript
* F03: Subagents (code-reviewer, security-reviewer, test-writer) und Skills (adr-schreiben, eval-fall-anlegen)
* F04: CI-Pipeline mit Hook-Tests, Formatierungsprüfung und Meldung geänderter Hooks, Tests und Pipeline; `.gitattributes` für einheitliche Zeilenenden; Pflichtprüfungen im Ruleset
* F05: Datenmodell und Schema mit Constraint-Tests; synthetische Testdaten (40 Artikel, 21 Kunden, 35 Kontakte, 77 Aktivitäten, 18 Rabattregeln, 24 Zuordnungen von Ersatzteil zu Anlage) und Richtlinien (Tonalität, Eskalation, Signatur, generierte rabatte.md); Befüllbefehl python -m db.seed mit Löschschutz (Bestätigung durch Hostnamen) und Konsistenzprüfungen; psycopg und pytest als Entwicklungsabhängigkeiten (requirements-dev.txt); der Job Schema-Tests führt tests/seed mit aus
* F06: MCP-Server-Grundgerüst „hoffmann-data“ (Python, Streamable HTTP, SDK mcp 2.3.0): Zugang nur mit Token (eigene Middleware, `Authorization: Bearer <token>`, jede Route ohne Token 401, WebSocket abgelehnt), Start nur mit gültigem Token, Host-/Origin-Schutz mit `MCP_SERVER_ALLOWED_HOSTS`, zustandsloser Transport; die acht Schnittstellen (fünf Tools, zwei Resources, ein Prompt) sind Platzhalter mit dem Fehler „noch nicht implementiert“; ADR 0003; neuer CI-Job MCP-Tests. Fortschrittsmeldungen bei länger laufenden Aufrufen gehören zu F10.

