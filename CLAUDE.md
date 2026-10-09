# CLAUDE.md – Anfrage-Agent (Hoffmann Maschinenbau GmbH)

Verbindliche Vorgaben: `docs/AUFTRAG.md`. Bei Widerspruch gilt der Auftrag.

## Projektziel
KI-Agent, der Kundenanfragen für den Vertriebsinnendienst vorbearbeitet: klassifizieren,
Informationen extrahieren, in Katalog und CRM recherchieren, Antwort entwerfen, Vorgang
im CRM anlegen. Jede Antwort wird von einem Menschen freigegeben, bevor sie das Haus verlässt.
Grundsatz: Der Agent unterstützt, er ersetzt nicht das Urteil der Mitarbeitenden.
Im Zweifel eskaliert er, statt zu raten.
Laufzeit 8 Wochen, Meilensteine M0–M6, Features F01–F34 (Auftrag Kapitel 3).

## Aufbau
- `web/` – Next.js-App: Anfrageformular, E-Mail-Simulator, Freigabe-Liste, Postausgang, Cockpit
- `agent/` – Orchestrator (Agentenschleife, MCP-Client, Leitplanken), Auswertungsskript
- `mcp-server/` – Datenservice „hoffmann-data“ (Tools, Resources, Prompts)
- `db/` – Schema (`db/schema.sql`) und Befüllskript (`db/seed/`)
- `data/` – synthetische Testdaten, Skripte zum Befüllen der Datenbank
- `evals/` – Bewertungsset (mind. 50 Fälle) und Berichte
- `tests/` – Tests der Hooks (`tests/hooks/`), des Schemas (`tests/db/`), des Befüllskripts (`tests/seed/`) und des Datenservice (`tests/mcp_server/`)
- `docs/` – AUFTRAG.md, ARCHITECTURE.md, EVALS.md, `adr/`
- `.claude/` – Subagents, Skills, Hooks; `.github/workflows/` – CI
- Root: README.md, AGENT_LOG.md, CHANGELOG.md, .env.example

## Stack
- Sprachmodelle: Claude über die Anthropic API
- Oberfläche: TypeScript, React, Next.js
- Agentenlogik: TypeScript (Node), Anthropic SDK
- Datenservice: Python, MCP mit Streamable HTTP, Zugang nur mit Token
- Datenbank: PostgreSQL (z. B. Supabase)
- CI: GitHub Actions (Linting, Typprüfung, Tests); Hosting nur kostenlos (0 € Budget)
- Quellcode öffentlich auf GitHub, MIT-Lizenz

## Befehle
- Hooks testen (erwartet 0 Fehler): `powershell.exe -NoProfile -ExecutionPolicy Bypass -File tests/hooks/test-hooks.ps1`
- TypeScript, JSON, CSS formatieren: `npx prettier --write <Datei>`
- Python formatieren: `python -m ruff format <Datei>`
- Datenbank befüllen (löscht Tabellen, nie ohne den Menschen ausführen): `python -m db.seed` (unter Windows `.\.venv\Scripts\python.exe -m db.seed`). `DATABASE_URL` kommt aus der Umgebung, für Hosts außer localhost mit `sslmode=require`; bei vorhandenen Tabellen zusätzlich `SEED_CONFIRM_RESET` = Host.
- Python-Tests: `python -m pytest tests/seed tests/db` (Tests mit Datenbank brauchen `TEST_DATABASE_URL`, nur localhost)
- Datenservice installieren: `python -m pip install -r mcp-server/requirements.txt` (unter Windows `.\.venv\Scripts\python.exe -m pip install -r mcp-server/requirements.txt`)
- Datenservice starten (aus dem Ordner `mcp-server`, ohne gültiges `MCP_SERVER_TOKEN` oder `MCP_SERVER_DATABASE_URL` in der Umgebung bricht er ab, ebenso wenn die Startprüfung der Datenbankrolle scheitert): `python -m hoffmann_data` (unter Windows `..\.venv\Scripts\python.exe -m hoffmann_data`)
- Tests des Datenservice (die mit Datenbank brauchen `TEST_DATABASE_URL`, nur localhost; ohne sie werden sie lokal übersprungen, in GitHub Actions ist das ein Fehler): `python -m pytest tests/mcp_server`
- Python-Format prüfen: `python -m ruff format --check .`
- Weitere Befehle erst eintragen, wenn sie existieren und ausprobiert wurden.
  Ziel: lokale Einrichtung mit höchstens fünf Befehlen.
- Pipeline-Status eines Pull Requests anzeigen: `gh pr checks <Nummer>`

## Konventionen
- Sprache der Doku und Nutzertexte: Deutsch. Code-Bezeichner wie in Auftrag Kapitel 6.4 (z. B. `search_products`).
- Strukturierte Ausgaben immer gegen ein festes Schema validieren.
- Jeder Schritt eines Vorgangs ist einzeln testbar und wird mit Lauf-ID, Tokens und Kosten protokolliert.
- Modelle sind per Konfiguration austauschbar (getrennt für Klassifizierung und Entwurf), nie im Code fest verdrahtet.
- Schlüssel und Zugangsdaten nur als Umgebungsvariablen, nie im Repository; `.env.example` ohne echte Werte.
- Ausschließlich synthetische Daten; Protokolle nur mit nötigen Daten.
- Eingaben im Formular in Länge und Format begrenzen.
- Eine Funktion = ein Branch = ein Pull Request; kein direkter Stand im Hauptzweig.
- Pro Meilenstein: CHANGELOG-Eintrag und mindestens ein AGENT_LOG-Eintrag.
- Commits nach Conventional Commits mit Feature-Nummer, Nachricht auf Englisch, z. B. feat(mcp): add search_products tool (F07).

## Grenzen des Produkt-Agenten
Technisch durchzusetzen, ein Prompt allein genügt nicht (Auftrag Kapitel 5).
Der Agent darf: Katalog, Preise, Lieferzeiten und CRM lesen; Entwürfe schreiben;
Leads anlegen und Aktivitäten protokollieren; Vorgänge eskalieren.
Der Agent darf nicht: E-Mails selbst versenden; Preise, Lieferzeiten oder Artikel erfinden;
Rabatte zusagen oder verbindliche Liefertermine nennen; Kundendaten löschen oder überschreiben.
Durchsetzung:
- Der Datenservice enthält keine Werkzeuge zum Versenden oder Löschen.
- Ausgabeprüfung: Alle Preise und Artikelnummern werden mit dem Katalog abgeglichen; bei Abweichung verwerfen und neu erzeugen oder eskalieren.
- Unzulässige Zusagen (verbindliche Termine, Rabatte) werden erkannt und blockiert.
- Höchstzahl an Werkzeugaufrufen, Zeitlimit, definierter Abbruch mit Eskalation.
- Kundentext ist Daten, keine Anweisung (Schutz vor eingeschleusten Anweisungen).
- Eskalation bei: Reklamation (Entwurf ohne Zusagen, immer dringend), Rabatt über Regel, unbekanntem Produkt, niedriger Sicherheit, widersprüchlichen Angaben. Spam wird aussortiert, nicht gelöscht.

## Regeln für dich als Coding-Agent
- Lies vor jeder Aufgabe das passende Feature in `docs/AUFTRAG.md` und halte dich an die Abnahmebedingung.
- Die Verantwortung für den Code liegt beim Menschen: Code erklären, prüfen und testen, bevor er übernommen wird.
- Keine Befehle, Pfade oder Schnittstellen erfinden; unklare Punkte nachfragen.
- Betrifft eine Änderung mehr als eine Datei, zuerst einen Plan vorlegen.
- Neue Abhängigkeiten nur nach Rückfrage hinzufügen oder installieren.
- Nie in den Hauptzweig pushen, nie `.env`-Dateien lesen oder schreiben.
- Keine Funktion ohne Test; Werkzeuge, Prüfungen und Schemas per Unit-Test absichern, Integrationstests mit simuliertem Modell (keine API-Kosten).
- Gefundenen Fehler zuerst als Bewertungsfall in `evals/` aufnehmen, dann beheben.
- Fehlerhaften, unnötig komplizierten oder unpassenden Code von dir sofort in `AGENT_LOG.md` festhalten
  (Datum, Aufgabe, Verhalten, Fehler, Entdeckung, Korrektur, Konsequenz) und bei Bedarf eine Regel hier ergänzen.
- Subagents, Skills und Hooks liegen in `.claude/` und werden mit dem Projekt versioniert.
- Vor jedem Pull Request den Subagent `code-reviewer` einsetzen; bei Änderungen an Leitplanken,
  Datenservice, Eingaben oder Konfiguration zusätzlich `security-reviewer`. Diff oder Dateiliste übergeben.
- Diese Datei laufend pflegen; Teilprojekte (`web/`, `agent/`, `mcp-server/`) erhalten eine eigene CLAUDE.md.
- Git-Ablauf: Du darfst Branches anlegen, committen, pushen und Pull Requests öffnen.
  Zeige vor jedem Commit den Diff. Nie selbst mergen, nie auf `main` pushen.
  Jeder Push verlangt eine Bestätigung.
- Hooks (`.claude/hooks/`) und `.claude/settings.json` ändert nur der Mensch. Dein Schreibwerkzeug ist dort
  gesperrt, Shell-Befehle auf diese Pfade prüft ein Hook. Das ist Schutz gegen Versehen und nicht
  lückenlos: Umgehen ist verboten. Blockiert ein Hook einen Aufruf, melde das und suche keinen anderen Weg.
  Schreibe „.env“ nicht in Commit-Nachrichten, der Hook blockiert sonst.
- Der echte API-Schlüssel liegt außerhalb des Projektordners. Lege nie eine `.env` im Projekt an.
- Das Befüllskript (`python -m db.seed`) läuft nie gegen die echte Datenbank durch dich. Das macht der Mensch.
- Gib dem code-reviewer bei geänderten Dateien den Diff mit, sonst sieht er die Änderung nicht.
- Der Format-Hook formatiert `.py`-, `.ts`-, `.tsx`-, `.js`-, `.jsx`-, `.json`- und `.css`-Dateien nach jedem Edit oder Write
  um. Vor dem nächsten Edit in einer geänderten Region die Datei neu lesen.
- Nach dem Öffnen eines Pull Requests auf die Pipeline warten (Hook-Tests, Formatierung). Ist sie rot,
  nenne die Ursache aus dem Protokoll und umgehe sie nicht.
