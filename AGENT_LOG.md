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

### 2026-10-07 · F05 · Fehler in Testdaten, Löschschutz und Tests des Befüllskripts (Code von Claude Code)
- **Aufgabe:** Befüllskript für die Datenbank mit Stammdaten, Richtlinien und Löschschutz schreiben (F05 Teil 2).
- **Verhalten des Agenten:** Claude Code schrieb Testdaten, Lader, Konsistenzprüfungen, Löschschutz, Schreiber und Tests, jede Datei erst nach meiner Freigabe.
- **Fehler:**
  - Testdaten: Ein Aktivitätseintrag enthielt noch eine falsche Artikelnummer (FB-1008-H1) und einen Satz zur Systemlogik. Das README der Stammdaten behauptete, die Daten enthielten keine Hinweise für den Innendienst, obwohl ein Eintrag und die Hartmann-Notizen solche Hinweise enthielten.
  - Löschschutz: Die Fehlermeldung gab den tatsächlich verwendeten Host aus. Bei einer URL mit nicht kodiertem @ im Passwort hätte das Teile des Passworts in die Ausgabe gebracht.
  - Tests: Im erwarteten Tupel fehlte contact_email. Ein Rest `if False else None` stand im Code. Ein Test für rabatte.md bewies nicht, dass ein Abbruch durch den Löschschutz die Datei unverändert lässt. Ein Test suchte den Text "1 Zeilen" und hätte auch bei "21 Zeilen" bestanden.
- **Entdeckung:** Der KI-Assistent im Chat (nicht Claude Code) fand die Punkte beim Gegenlesen der Dateien vor meiner Freigabe, die Daten zusätzlich mit einem Prüfskript, das ich ausführte. code-reviewer und security-reviewer laufen erst vor dem Pull Request, ihre Befunde trage ich nach.
- **Korrektur:** Daten und README korrigiert, Meldung ohne Host, mehrere @ in der URL werden abgelehnt, ein Vergleichstest gegen den libpq-Parser, die betroffenen Tests korrigiert bzw. neu geschrieben.
- **Verifikation (durch den KI-Assistenten im Chat, nicht durch Claude Code):** Im Sandbox-Lauf des Assistenten liefen 565 Tests (tests/db und tests/seed) gegen einen lokalen Postgres 16, alle bestanden. Ein Lauf von `python -m db.seed` gegen eine leere Datenbank befüllte alle sechs Tabellen. Ein zweiter Lauf ohne SEED_CONFIRM_RESET und einer mit falschem Wert brachen mit Exit 2 ab, ohne etwas zu löschen. Mit dem Host als Wert wurde neu aufgebaut. Der erste Lauf in der CI mit Postgres 17 steht noch aus.
- **Konsequenz:** Meldungen des Skripts geben nie Teile der Verbindung aus, per Test abgesichert. Tests, die einen Schutz belegen sollen, müssen den Fehlschlag selbst nachweisen und nicht nur das Ergebnis.

### 2026-10-07 · F05 · Fehler in eigenen Datenbank-Tests und im Schema (Code von Claude Code, ein Fehler vom KI-Assistenten im Chat)
- **Aufgabe:** Tests für das Datenbankschema und die Testdaten schreiben (F05, tests/db/).
- **Verhalten des Agenten:** Ich schrieb die ersten Tests, die Beziehungen, Einschränkungen (CHECK, UNIQUE)
  und Zeilensicherheit (RLS) prüfen sollten.
- **Fehler:**
  - Eigene Fehler, beim Durchlesen selbst bemerkt: `getattr(make, table[:-1])` hätte bei `activities`
    den Namen `activitie` ergeben. `role.as_string()` funktioniert in psycopg 3 ohne Kontext nicht.
  - Befunde der Reviewer (code-reviewer, security-reviewer) an den ersten Tests:
    - `rejected()` prüfte nur die Fehlerklasse, nicht den Namen des Constraints. Ein Test konnte
      aus dem falschen Grund bestehen.
    - Der erste `rejected`-Aufruf lief ohne Savepoint. Ein fehlender Constraint hätte committet.
    - Tests konnten in der CI stumm übersprungen werden (Exit 0).
    - Die Test-URL wurde nicht auf den Host geprüft.
    - RLS wurde nur als Flag geprüft, nicht in der Wirkung.
    - Es fehlten Tests für NOT NULL, Standardwerte, Löschschutz, Identity und doppelte E-Mail
      über Kunden.
  - Tests geschrieben, aber nicht ausgeführt, weil pytest und psycopg lokal fehlten. Ausgeführt
    wurden sie nur im Sandbox-Lauf des KI-Assistenten im Chat und später in der CI.
  - Fehler vom KI-Assistenten im Chat (nicht von Claude Code): `max_discount_percent` war
    `numeric(4,2)`, dadurch war der Wert 100 nicht speicherbar.
- **Entdeckung:** Die zwei Test-Fehler fand ich beim Durchlesen meines Codes. Die Reviewer-Befunde kamen
  von code-reviewer und security-reviewer. Den Typfehler bei `max_discount_percent` bemerkte ich selbst.
- **Korrektur:** Alle genannten Punkte behoben, `max_discount_percent` ist jetzt `numeric(5,2)`.
- **Verifikation (durch den KI-Assistenten im Chat, nicht durch Claude Code):** Im Sandbox-Lauf des
  Assistenten, nicht im Projekt, liefen alle 207 Tests gegen einen lokalen Postgres 16 (190
  Datenbank-Tests, 17 ohne Datenbank), alle bestanden. Absichtlich beschädigtes Schema (Preis-CHECK,
  RLS, NOT NULL, ON DELETE CASCADE) wurde von den Tests erkannt.
- **Konsequenz:** Der erste Lauf in der CI mit Postgres 17 steht noch aus. Erst er zeigt, ob die Tests
  auch außerhalb der Chat-Sandbox bestehen.

### 2026-10-06 · F02 · Fehlalarm: Glob mit Platzhalter blockiert (Code vom KI-Assistenten im Chat)
- **Aufgabe:** Härtung des .env-Hooks gegen Platzhalter wie `cat *`.
- **Verhalten des Agenten:** Der KI-Assistent im Chat (nicht Claude Code) sperrte jeden reinen Platzhalter,
  auch bei den Werkzeugen Glob und Grep.
- **Fehler:** Glob mit `docs/adr/*` und Grep mit dem Filter `*` wurden blockiert. Die Tests für normale Aufrufe
  deckten nur Shell-Befehle ab, keine Werkzeug-Aufrufe.
- **Entdeckung:** Claude Code meldete beim Schreiben eines ADR, dass sein Glob blockiert wurde. Ich gab die Meldung
  an den KI-Assistenten im Chat weiter, der den Fehler nachstellte.
- **Korrektur:** Glob und Grep lassen reine Platzhalter zu, `.env*` bleibt gesperrt. 14 neue Tests.
- **Konsequenz:** Tests für normale Aufrufe müssen jedes Werkzeug abdecken, nicht nur die Shell.

### 2026-10-06 · F02 · Lücken in den Hooks (Code vom KI-Assistenten im Chat)
- **Aufgabe:** Hooks für den Schutz von .env-Dateien und für Pushes auf main (F02).
- **Verhalten des Agenten:** Der KI-Assistent im Chat (nicht Claude Code) lieferte Hook-Skripte,
  die Befehle nur über Textmuster prüften.
- **Fehler:** Erste Fassung: vier Umgehungen (git -c … push, gh pr merge, .e*, zusammengesetzter Name),
  außerdem war block-gh-merge.ps1 nicht committet, obwohl settings.json darauf verwies. Die Korrektur
  brachte neue Fehler: Der Tokenizer las nur das erste Wort jedes Teilbefehls und ließ Hüllen
  (bash -c, cmd /c, $(...)) durch. Dazu kamen fehlende Platzhalter (.env.*), Schreib-Aliase (sc, ni, ...)
  und NotebookEdit.
    Zwei der überarbeiteten Hooks (block-main-push.ps1, block-gh-merge.ps1) blieben nach der
  Korrektur ungespeichert, obwohl die zugehörigen Tests und die settings.json schon committet waren.
- **Entdeckung:** Die erste Runde fand der security-reviewer beim ersten Einsatz (F03) durch Lesen des
  Quelltexts, ein Testlauf bestätigte sie. Das fehlende Skript fiel dem KI-Assistenten im Chat beim Lesen
  meiner Ausgabe von git status --short auf (Eintrag mit ??). Die zweite Runde fanden code-reviewer und
  security-reviewer, und jede Behauptung wurde vor der Korrektur als roter Test festgehalten.
    Die zwei ungespeicherten Hooks bemerkte ich bei git status --short vor dem Push.
  Gegen den alten Stand waren 76 Tests rot, gegen den neuen sind es 0.
- **Korrektur:** Hooks neu geschrieben, 333 Tests, ein Test für die Verweise der settings.json, Edit-Sperre
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