# Changelog

## [Unveröffentlicht]

### Geändert
- Hooks gehärtet (Issue #4): git push mit globalen Optionen, gh pr merge, schreibende gh-api-Aufrufe,
  Platzhalter und zusammengesetzte Namen für .env, Schreibzugriffe auf Hooks und Einstellungen
- Edit-Sperre für Hooks und Einstellungen, Rückfrage vor git push und Sperre für gh pr merge
- test-writer ohne Bash, Hook-Tests nach tests/hooks verschoben
- Hooks erkennen Hüllen und Unterausdrücke, Platzhalter und Windows-Namensformen für .env; Schreibzugriffe auf Hooks nur noch lesend erlaubt (Positivliste); überschreibende git-Befehle blockiert; Hook-Aufrufe fehlersicher

### Hinzugefügt
- F01: Repository, Ordnerstruktur, CLAUDE.md, Entwicklungsprotokoll
- F02: Hooks für Claude Code (Schutz für .env-Dateien und main, automatische Formatierung) mit Testskript
- F03: Subagents (code-reviewer, security-reviewer, test-writer) und Skills (adr-schreiben, eval-fall-anlegen)