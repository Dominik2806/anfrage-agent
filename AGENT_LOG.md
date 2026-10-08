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

### 2026-10-08 · F06 · Befunde der Reviewer zum Datenservice (Code von Claude Code)
- **Aufgabe:** Review von F06 vor dem Pull Request mit code-reviewer und security-reviewer
  (mcp-server/hoffmann_data, tests/mcp_server, Doku, CI-Job).
- **Verhalten des Agenten:** Der Datenservice war umgesetzt, 90 von 90 Tests grün. Beide Reviewer fanden
  keine Umgehung des Zugangsschutzes und kein Token-Leck, aber Lücken:
  - M1: nur `mcp` gepinnt, obwohl starlette, uvicorn und mcp-types direkt importiert werden.
  - M2: Anfragegröße nur durch das SDK-Standardlimit (4 MiB) begrenzt.
  - M3: Leere optionale Variablen (so steht es in `.env.example`) ließen den Start scheitern.
  - M4/M5: Fehlercode -32603 stand in der README, aber in keinem Test; der Docstring im Prompt-Test
    wiederholte die widerlegte Aussage.
  - N1: Token `"a" * 32` wurde akzeptiert (ein Test belegte es sogar).
  - N2: `BearerTokenMiddleware("")` war erlaubt.
  - N5: README und ADR behaupteten, Leerzeichen am Rand des Headerwerts würden abgelehnt; das entfernt über
    ein echtes Netz schon der Webserver, der Test lief nur auf App-Ebene.
  - N6/N7: kein Test für doppelte Authorization-Header, `compare_digest` nur bei falschem Token geprüft,
    mehrere zu schwache Tests (Exit-Code `!= 0` statt 1, unbekanntes Werkzeug mit „oder“, kein Origin-Test
    für Nicht-Loopback).
  - Kleinigkeiten: „~40 Zeilen“ im ADR, Zeichensatz-Satz in der README, irreführender Kommentar in `ci.yml`.
- **Fehler:** Tests und Doku behaupteten mehr, als der Code oder der Test belegte. Die Optionalität der
  Variablen widersprach der eigenen Vorlage. Zusätzlich führte Claude Code in Etappe (d) ein lesendes
  `git diff` aus, obwohl „keine Befehle“ abgesprochen war.
- **Entdeckung:** Reviews durch die Subagents code-reviewer und security-reviewer; die Befunde zu M3 und N1
  trafen unabhängig voneinander beide.
- **Korrektur:** Entscheidungen des Menschen: leere optionale Variablen gelten als nicht gesetzt; Token mit
  mindestens 10 verschiedenen Zeichen; leeres Token in der Middleware ein `ValueError`; doppelte
  Authorization-Header ergeben 401, `compare_digest` genau einmal auch ohne Header; Fehlercodes in den Tests;
  Anfragen höchstens 256 KiB (413); starlette, uvicorn und mcp-types fest gepinnt; Doku zu Header-Form,
  Zeichensatz und Grenzen präzisiert. Zuerst die Tests (Teil 1), danach der Code (Teil 2).
  Offen und im ADR 0003 vermerkt: GET-Strom auf `/mcp` (F13), getrennte Token und technische Freigabe für
  `create_lead` (F08).
- **Konsequenz:** Doku und Tests nicht stärker formulieren, als sie belegt sind. Ein Befehl, den der Mensch
  ausgeschlossen hat, wird auch dann nicht ausgeführt, wenn er nur lesend ist.

### 2026-10-08 · F06 · Falsche Aussage über die Fehlerweitergabe bei Prompts im SDK (Code von Claude Code)
- **Aufgabe:** Platzhalter für die acht Schnittstellen des Datenservice, jeder mit dem Fehler „noch nicht
  implementiert“, auch der Prompt `antwort_entwurf`.
- **Verhalten des Agenten:** In der Lesephase stand die Aussage, das SDK gebe bei Prompts jede
  Ausnahme als `ValueError(str(e))` mit unserem Text weiter. Der Prompt-Platzhalter warf daraufhin eine
  einfache `NotImplementedError`.
- **Fehler:** Die Aussage stützte sich nur auf `get_prompt` in `server.py`. Dass `prompts/base.py` (`render`)
  jede Ausnahme außer `MCPError` vorher durch `ValueError("Error rendering prompt ...")` ersetzt, wurde nicht gelesen.
  Der Text ging verloren, der Client bekam Code 0 und die Allgemeinmeldung.
- **Entdeckung:** Der Test `test_prompt_placeholder_returns_an_error_not_data` war nach der Implementierung rot
  (89 von 90 grün). Der Test war vorher als offenes Risiko vermerkt.
- **Korrektur:** Der Prompt wirft `MCPError(code=INTERNAL_ERROR, message=...)`, die das SDK unverändert
  durchreicht (`render`, `get_prompt` und der Dispatcher geben sie weiter). Importpfade am Quelltext geprüft.
  Der Test blieb unverändert.
- **Konsequenz:** Verhalten fremder Bibliotheken nicht aus einer Stelle im Quelltext folgern, sondern mit einem
  Test belegen. Regel in `mcp-server/CLAUDE.md` ergänzt.

### 2026-10-08 · F02 · Hooks blockieren harmlose Befehle mit eckiger Klammer
- **Aufgabe:** Fehlalarm der Hooks klären. Befehle mit einer einzelnen eckigen Klammer wurden blockiert,
  obwohl sie weder Schlüsseldateien noch die Schutzpfade betrafen.
- **Verhalten des Agenten:** block-env.ps1 und block-guard-paths.ps1 werteten einen Fehler beim
  Platzhaltervergleich als Treffer und blockierten den Aufruf.
- **Fehler:** -like wirft einen Fehler, wenn ein [ kein passendes ] hat. Der catch-Zweig wertete jeden
  Fehler als Treffer. Beide Skripte hatten dieselbe Logik.
- **Entdeckung:** Mehrere harmlose Befehle wurden in Claude Code blockiert. Die Testsuite fand das nicht,
  weil sie die Hooks direkt aufruft und nicht über Claude Code. Die erste Vermutung des KI-Assistenten im
  Chat (Zeilen mit einer 2) war unbelegt und wurde zurückgenommen. Die Ursache ist das [.
- **Korrektur:** Zuerst zwei rote Tests (echo mit eckiger Klammer, Python-Listenkomprehension), dann die
  Funktion Get-SafePattern in beiden Hooks. Ungleich viele [ und ] werden wörtlich verglichen.
  Danach 349 Tests, 0 Fehler. Code vom KI-Assistenten im Chat.
- **Konsequenz:** Keine neue Regel. Die Restlücken der Hooks bleiben in Issue #6. Die Suite prüft die
  Hooks direkt, deshalb gehört nach Hook-Änderungen ein echter Befehl in Claude Code dazu.

### 2026-10-07 · F05 · Befunde der Reviewer zum Befüllskript (Code von Claude Code)
- **Aufgabe:** Review von F05 Teil 2 vor dem Pull Request mit code-reviewer und security-reviewer.
- **Verhalten des Agenten:** Claude Code hatte Löschschutz, Schreiber, Einstiegspunkt und Tests geliefert, jede Datei nach meiner Freigabe, die Tests ohne Datenbank lokal grün.
- **Fehler:**
  - Sicherheit: Das Skript verlangte keine verschlüsselte Verbindung. Ohne sslmode nimmt libpq prefer und fällt auf unverschlüsselt zurück. Der Datenbankname war nicht begrenzt: Bei einem Passwort mit unkodiertem / hätten Passwortreste oder Steuerzeichen in der Ausgabe gestanden.
  - Tests: Tests wurden stumm übersprungen, wenn ein Import in writer.py scheiterte (importorskip), die CI wäre grün geblieben. Alle Datenbank-Tests liefen unter einer äußeren Transaktion, der Produktionspfad mit Autocommit, echtem COMMIT und ROLLBACK war nicht getestet. Die Prüfung ließ eine Verbindung in einer offenen Transaktion zu, die nie committet worden wäre. Die Meldung "nicht verändert" konnte bei einem Verbindungsabbruch während des COMMIT falsch sein.
  - Daten: Ein Wartungsvertrag war für ein Jahr bestellt, der Einsatz "im Rahmen des Vertrags" lag nach dessen Ablauf. Ein Eintrag von 2018 sagte "inzwischen ausgelaufen", ein Pronomen passte nicht zum Kontakt, ein README-Satz widersprach den Daten. Das README behauptete Prüfungen (Beträge, Sortierung, .example), die kein Test absicherte.
- **Entdeckung:** code-reviewer (vier mittlere Befunde) und security-reviewer (zehn Befunde, keine hohen) lasen den Branch vor dem Pull Request. Der KI-Assistent im Chat prüfte die Behauptungen nach, etwa das Fehlen von sslmode im Code und den Vertrag in den Daten.
- **Korrektur:** In drei Gruppen mit je einem Commit: Daten und Datentests, Autocommit strikt mit Test des Produktionspfads und Meldung bei ungewissem Zustand, sslmode und Datenbankname im Guard. Die niedrigen Befunde sind als Liste festgehalten und noch offen.
- **Verifikation (durch den KI-Assistenten im Chat, nicht durch Claude Code):** Im Sandbox-Lauf des Assistenten liefen 698 Tests (tests/db und tests/seed) gegen einen lokalen Postgres 16, alle bestanden. Eine Gegenprobe ohne die Transaktion um das Schreiben ließ vier Tests scheitern. Ich führte python -m db.seed gegen die echte Datenbank aus: erster Lauf auf leerer Datenbank, zweiter ohne Bestätigung (Exit 2, nichts gelöscht), dritter mit dem Host als Bestätigung (neu aufgebaut, gleiche Zeilenzahlen).
- **Konsequenz:** importorskip nur für optionale Pakete, nie für eigenen Code. Der Produktionspfad bekommt einen eigenen Test, nicht nur die Testvariante. Was die Doku über Prüfungen behauptet, braucht einen Test. Reviewer laufen vor dem Pull Request.

### 2026-10-07 · F05 · Fehler in Testdaten, Löschschutz und Tests des Befüllskripts (Code von Claude Code)
- **Aufgabe:** Befüllskript für die Datenbank mit Stammdaten, Richtlinien und Löschschutz schreiben (F05 Teil 2).
- **Verhalten des Agenten:** Claude Code schrieb Testdaten, Lader, Konsistenzprüfungen, Löschschutz, Schreiber und Tests, jede Datei erst nach meiner Freigabe.
- **Fehler:**
  - Testdaten: Ein Aktivitätseintrag enthielt noch eine falsche Artikelnummer (FB-1008-H1) und einen Satz zur Systemlogik. Das README der Stammdaten behauptete, die Daten enthielten keine Hinweise für den Innendienst, obwohl ein Eintrag und die Hartmann-Notizen solche Hinweise enthielten.
  - Löschschutz: Die Fehlermeldung gab den tatsächlich verwendeten Host aus. Bei einer URL mit nicht kodiertem @ im Passwort hätte das Teile des Passworts in die Ausgabe gebracht.
  - Tests: Im erwarteten Tupel fehlte contact_email. Ein Rest `if False else None` stand im Code. Ein Test für rabatte.md bewies nicht, dass ein Abbruch durch den Löschschutz die Datei unverändert lässt. Ein Test suchte den Text "1 Zeilen" und hätte auch bei "21 Zeilen" bestanden.
- **Entdeckung:** Der KI-Assistent im Chat (nicht Claude Code) fand die Punkte beim Gegenlesen der Dateien vor meiner Freigabe, die Daten zusätzlich mit einem Prüfskript, das ich ausführte. code-reviewer und security-reviewer laufen erst vor dem Pull Request, ihre Befunde trage ich nach.
- **Korrektur:** Daten und README korrigiert, Meldung ohne Host, mehrere @ in der URL werden abgelehnt, ein Vergleichstest gegen den libpq-Parser, die betroffenen Tests korrigiert bzw. neu geschrieben.
- **Verifikation (durch den KI-Assistenten im Chat, nicht durch Claude Code):** Im Sandbox-Lauf des Assistenten liefen 565 Tests (tests/db und tests/seed) gegen einen lokalen Postgres 16, alle bestanden. Ein Lauf von `python -m db.seed` gegen eine leere Datenbank befüllte alle sechs Tabellen. Ein zweiter Lauf ohne SEED_CONFIRM_RESET und einer mit falschem Wert brachen mit Exit 2 ab, ohne etwas zu löschen. Mit dem Host als Wert wurde neu aufgebaut. Die CI lief danach grün mit Postgres 17 (Job Schema-Tests, 698 Tests aus tests/db und tests/seed).
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