# Entwicklungsprotokoll

Dieses Protokoll hält fest, wo der Coding-Agent (Claude Code) fehlerhaften,
unnötig komplizierten oder unpassenden Code erzeugt hat, wie das bemerkt wurde
und welche Konsequenz daraus folgte. Neueste Einträge stehen oben.

## Vorlage

### JJJJ-MM-TT · Fxx · Kurztitel
- **Aufgabe:** Was sollte Claude Code tun?
- **Verhalten des Agenten:** Was hat er tatsächlich getan?
- **Fehler:** Was war falsch, unnötig kompliziert oder unpassend?
- **Entdeckung:** Wie wurde es bemerkt? (Review, Test, Laufzeitfehler ...)
- **Korrektur:** Was wurde geändert?
- **Konsequenz:** Neue Regel in CLAUDE.md, neuer Test, neuer Hook oder Bewertungsfall

## Einträge

### 2026-10-06 · F02 · Lücken in den Hooks (Code vom KI-Assistenten im Chat)
- **Aufgabe:** Hooks für den Schutz von .env-Dateien und für Pushes auf main (F02).
- **Verhalten des Agenten:** Der KI-Assistent im Chat (nicht Claude Code) lieferte Hook-Skripte,
  die Befehle nur über Textmuster prüften.
- **Fehler:** Erste Fassung: vier Umgehungen (git -c … push, gh pr merge, .e*, zusammengesetzter Name),
  außerdem war block-gh-merge.ps1 nicht committet, obwohl settings.json darauf verwies. Die Korrektur
  brachte neue Fehler: Der Tokenizer las nur das erste Wort jedes Teilbefehls und ließ Hüllen
  (bash -c, cmd /c, $(...)) durch. Dazu kamen fehlende Platzhalter (.env.*), Schreib-Aliase (sc, ni, ...)
  und NotebookEdit.
- **Entdeckung:** Die erste Runde fand der security-reviewer beim ersten Einsatz (F03) durch Lesen des
  Quelltexts, ein Testlauf bestätigte sie. Das fehlende Skript fiel dem KI-Assistenten im Chat beim Lesen
  meiner Ausgabe von git status --short auf (Eintrag mit ??). Die zweite Runde fanden code-reviewer und
  security-reviewer, und jede Behauptung wurde vor der Korrektur als roter Test festgehalten.
- **Korrektur:** Hooks neu geschrieben, 239 Tests, ein Test für die Verweise der settings.json, Edit-Sperre
  und Rückfrage vor git push, Aufrufe fehlersicher, test-writer ohne Bash.
- **Konsequenz:** Hooks und Einstellungen ändert nur der Mensch (CLAUDE.md). Hooks sind Schutz gegen
  Versehen, keine Sandbox. Der Schlüssel liegt außerhalb des Projekts. Offen: grep -r KEY ., Pfade aus
  Teilen, ältere Hooks über git switch, pull oder merge (eigenes Issue).

### 2026-10-05 · F03 · Falsche Aussage über den Zustand des Repositorys
- **Aufgabe:** Plan für F03 (Subagents und Skills) entwerfen.
- **Verhalten des Agenten:** Im Plan stand, evals/ existiere noch nicht.
- **Fehler:** evals/ existiert seit F01 (mit README.md). Der Plan beschrieb den Zustand
  falsch, ohne ihn zu prüfen.
- **Entdeckung:** Der KI-Assistent im Chat (nicht Claude Code) wies beim Gegenlesen des
  Plans darauf hin, dass evals/ seit F01 existiert.
- **Korrektur:** Plan wurde korrigiert, Claude sollte vor Aussagen über das Repository
  das Verzeichnis prüfen.
- **Konsequenz:** Keine neue Regel nötig. Der Fehler fiel im Plan Mode auf, bevor eine
  Datei geschrieben wurde.