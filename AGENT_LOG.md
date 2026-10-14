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

### 2026-10-14 · F10 · Hook `block-guard-paths` blockierte einen reinen Lesebefehl
- **Aufgabe:** In `tests/mcp_server/test_progress_token_sources.py` die Namen und Zeilen zweier Tests finden (null, 300 KiB), um sie umzuschreiben.
- **Verhalten des Agenten:** Ein Bash-Aufruf mit `grep` und `sed -n` auf diese Testdatei. Der Befehl wörtlich:
  `cd /c/Users/sobek/Desktop/KI_BearbeitungVonKundenanfragen/anfrage-agent && grep -n "null\|300 KiB\|300 \* 1024\|def test_" tests/mcp_server/test_progress_token_sources.py | sed -n 1,60p`.
  Der Hook `block-guard-paths.ps1` blockierte ihn mit: „Blockiert: Hooks und Einstellungen aendert nur der Mensch. Dieser Befehl wuerde .claude/hooks oder die settings-Dateien veraendern.“
- **Fehler:** Kein Fehler des Agenten. Der Befehl las nur eine Testdatei und berührte weder `.claude/hooks` noch die Einstellungen. Die Ursache der Blockade ist offen und nicht
  untersucht; auffällig ist nur, dass der Befehl `sed -n` enthielt. Ob das der Auslöser war, ist nicht geprüft.
- **Entdeckung:** Meldung des Hooks im Lauf.
- **Korrektur:** Der Agent hat den Hook nicht umgangen. Er hat den Befehl dem Menschen wörtlich genannt und die Datei mit den Werkzeugen Grep und Read gelesen. Keine Änderung an
  Hooks (nur der Mensch ändert sie).
- **Konsequenz:** Möglicher zweiter Fehlalarm der Hooks (der erste: `block-env`, siehe unten); Eintrag im Sammel-Issue zur Prüfung durch den Menschen. Regel in CLAUDE.md gilt: blockiert ein
  Hook, melden und keinen anderen Weg suchen.

### 2026-10-14 · F10 · `progressToken` des Clients ohne Längengrenze zurückgeschickt
- **Aufgabe:** Fortschrittsmeldungen des Datenservice einbauen (F10); Eingaben begrenzen gilt als Projektregel (CLAUDE.md, Auftrag 11.3).
- **Verhalten des Agenten:** Der Reporter sendete jedes Mal, wenn das SDK einen `progressToken` erkannte. Der Token kommt in jede Meldung zurück; ADR 0007
  nannte die fehlende Längengrenze in der ersten Fassung nur als „offen“.
- **Fehler:** Ein vom Client geschickter Wert wurde ohne Begrenzung in unsere Antworten gespiegelt (bis zur Anfragegrenze von 256 KiB, bis zu dreimal). Mit
  einem Client, der nicht liest, hielt das auf dem modernen Weg Worker-Thread und lesende Verbindung bis zum Zeitlimit je Meldung fest.
- **Entdeckung:** code-reviewer und security-reviewer fanden es unabhängig voneinander (mittel).
- **Korrektur:** `progress_for` in `progress.py` begrenzt den Token (Zeichenkette bis 64 Zeichen, Ganzzahl bis 2^53 − 1 im Betrag, alles andere still ohne Fortschritt); Tests und
  Mutationen für beide Grenzen. Die erste Fassung las den Token aus `ctx.request_context.meta`. Der Beleg-Test prüfte nur, dass der Wert „irgendwo in meta“ steht, nicht unter
  welchem Schlüssel; die Doku sagte „belegt“. Das Nachreview wies darauf hin, dass das SDK den Token aus den rohen `params` zurückschickt und `meta` eine zweite Sicht ist, die abweichen
  kann (ein Extra-Schlüssel `progress_token`). Die zweite Fassung prüft den rohen Wert mit `progress_token_from_params` und hat Tests für beide Schlüssel; der Beleg-Test prüft
  jetzt Schlüssel und Wert je Typ. Mutationsprüfung (lokal, Skript nicht versioniert) nach der zweiten Fassung: 34 Mutationen, alle entdeckt, keine überlebt, kein Fehler. M32 (den
  Token wieder aus `meta` lesen) fangen `test_progress_token_sources.py` (HTTP, beide Wege) und der Unit-Test in `test_progress_token_limits.py`.
- **Konsequenz:** Ein Wert, den das SDK oder der Client zurückspiegelt, wird beim Entwurf wie jede Eingabe behandelt (Länge und Typ begrenzen), nicht erst nach dem Review. Ein Beleg-Test
  prüft die Behauptung, die die Doku aufstellt, nicht eine schwächere (hier: Schlüssel und Wert, nicht „der Wert kommt irgendwo vor“).

### 2026-10-14 · F10 · Mutation M26 fand eine Testlücke: `asyncio.CancelledError` im Reporter war ungeprüft
- **Aufgabe:** Reporter für Fortschrittsmeldungen (`progress.py`) schreiben, der Sendefehler abfängt und sich abschaltet.
- **Verhalten des Agenten:** Claude Code fing `(Exception, asyncio.CancelledError)` ab, ohne dass ein Test die Abbruchausnahme je auslöste. Die
  vier Fehlerklassen der Tests waren alle `Exception`-Unterklassen. Der Agent vermutete selbst, die Klausel könne toter Code sein, weil
  `concurrent.futures.CancelledError` eine `Exception` ist.
- **Fehler:** Eine Vermutung über das Verhalten von `anyio.from_thread.run` stand im Code (und im Plan), ohne Beleg und ohne Test.
- **Entdeckung:** Die Mutationsprüfung (M26: nur `except Exception`; lokal ausgeführt, das Skript ist nicht versioniert) lief grün durch, die Mutation überlebte.
- **Korrektur:** Ein Beleg-Test (`test_progress_cancellation.py`) zeigte, dass im Worker-Thread `asyncio.CancelledError` ankommt (eine `BaseException`);
  der Zweig ist nötig. Ein Test mit dem echten Reporter (`test_progress_unit.py`) tötet M26; der Kommentar in `progress.py` verweist auf den Beleg.
- **Konsequenz:** Die Regel „Verhalten des SDKs nicht aus dem Quelltext folgern, sondern mit einem Test belegen“ steht schon in `mcp-server/CLAUDE.md`; sie wurde
  hier für den Abbruchfall nicht befolgt. Jede Mutation muss einen konkreten Test töten, Lücken werden vor der Doku geschlossen.

### 2026-10-14 · F10 · Testhilfe: Server-Thread auf der Windows-Standardschleife (Proactor) hing beim Beenden
- **Aufgabe:** Eine Testhilfe schreiben, die den Datenservice mit uvicorn im Thread auf `127.0.0.1` startet (Port-Test, Ausnahme zur Portregel, ADR 0007).
- **Verhalten des Agenten:** `serve_on_loopback` ließ den Server mit `server.run` laufen (uvicorn nimmt unter Windows die Standardschleife) und setzte
  nach 10 s `force_exit` als Notausgang. Die Standardschleife unter Windows und ihre Folgen für das Beenden hatte der Agent nicht bedacht.
- **Fehler:** Unter Windows ist die Standardschleife die `ProactorEventLoop`. Ihr `_call_connection_lost` überspringt bei einem `ConnectionResetError` im
  `shutdown()` das `sock.close()` und `Server._detach`; `wait_closed()` wartete dann ohne Ende, `lifespan.shutdown` wurde nie gesendet, `force_exit` half nicht.
  Der Fehler trat nur in einem Teil der Läufe auf (Messreihe des Menschen: 5 von 20).
- **Entdeckung:** Messreihe des Menschen (Teardown 15,8 s); eine schrittweise erweiterte Diagnose in der Assertion-Meldung (offene Tasks mit Warte-Kette,
  uvicorn-Zähler, Lifespan-Nachrichten) und das Lesen der Quellen von Python 3.13 und uvicorn belegten die Kette.
- **Korrektur:** Die Testhilfe läuft auf der `SelectorEventLoop` (`asyncio.Runner(loop_factory=...)`); der Docstring nennt die belegte Ursache; die
  Assertion blieb unverändert streng und die Diagnose als kompakte Form. Mehrere Läufe in Folge liefen sauber (Angabe des Menschen).
- **Konsequenz:** Eine Ursache wird belegt, bevor eine Frist oder ein Zeitlimit „hilft“. Erwartung (die CI zeigt es): Der Linux-Runner hat das Problem nicht.

### 2026-10-14 · F10 · Hook `block-env` blockierte einen Befehl ohne Zugriff auf eine `.env`
- **Aufgabe:** SDK-Quelltexte (mcp, starlette) mit einem Shell-Befehl lesen, um den Wert von `_SSE_PING_INTERVAL` und die Client-API zu klären.
- **Verhalten des Agenten:** Ein Bash-Aufruf mit `cd …/.venv/Lib/site-packages && grep … ; ls … ; grep … ; cat ../../../../ruff.toml` wurde vom Hook mit „Zugriff auf
  .env-Dateien ist in diesem Projekt nicht erlaubt“ blockiert. Der Befehl las keine `.env`-Datei.
- **Fehler:** Kein Fehler des Agenten. Der Hook meldete einen Treffer, den ich nicht erklären kann; die Ursache ist offen (Verdacht: ein Muster, das einen Teil
  des Pfades oder des Befehls wie einen `.env`-Namen liest; nicht untersucht).
- **Entdeckung:** Meldung des Hooks im Lauf.
- **Korrektur:** Der Agent hat den Hook nicht umgangen, den Befehl dem Menschen wörtlich genannt und die Dateien mit dem Read-Werkzeug gelesen. Keine
  Änderung an Hooks (nur der Mensch ändert sie).
- **Konsequenz:** Möglicher Fehlalarm des Hooks; Eintrag im Sammel-Issue zur Prüfung durch den Menschen. Regel in CLAUDE.md gilt: blockiert ein Hook, melden und
  keinen anderen Weg suchen.

### 2026-10-13 · F09 · Mutationsskript: Endlosschleife durch eine zu allgemein beschriebene Mutation (Auftrag vom KI-Assistenten im Chat, Umsetzung von Claude Code)
- **Aufgabe:** Das Mutationsskript für die Nachbesserung um Mutationen M42 bis M62 erweitern. Eine davon lautete „Abschlussmarke immer
  mit Nummer 0“.
- **Verhalten des Agenten:** Der Auftrag des KI-Assistenten im Chat beschrieb die Mutation nur allgemein und nannte die Stelle nicht. Claude
  Code verankerte sie in `_closing_mark` (`return f"<<<ENDE-DATEN-{n}>>>"` → feste Nummer 0), ohne zu prüfen, wo `_closing_mark` benutzt
  wird. Beim Lauf blieb das Skript hängen, der Rechner war ausgelastet (Angabe des Menschen).
- **Fehler:** `_marks` nimmt `_closing_mark(n)` auf, und `_free_number` erhöht `n`, solange irgendeine Marke im Text vorkommt. Hängt
  eine Marke nicht mehr von `n` ab und steht sie im Eingabetext (die Fälschungs-Tests tun das), endet die Schleife nie. Claude Code
  hatte die Verwendung von `_closing_mark` vor dem Lauf nicht auf diese Schleife geprüft.
- **Entdeckung:** Lauf des Menschen: Das Skript hing bei der Mutation.
- **Korrektur:** Die Mutation ist neu verankert (nur die ausgegebene Abschlussmarke in `build_messages` trägt die feste Nummer 0;
  `_closing_mark`, `_marks` und `_free_number` bleiben unverändert). Das Skript hat ein Zeitlimit je Mutation (`-TimeoutSeconds`, Standard
  300, der Prozessbaum wird beendet; ein Hängen zählt als Erkennung, Ergebnis „rot (Zeitlimit)“) und stellt die Datei auch bei Strg+C aus den
  Originalbytes wieder her (Byte-Hash).
- **Konsequenz:** Eine Mutation, die einen Wert ändert, von dem eine Schleifenbedingung abhängt, wird vor dem Lauf auf die Schleife
  geprüft. Jeder Testlauf eines Mutationsskripts hat ein Zeitlimit.

### 2026-10-13 · F09 · Mutationsprüfung von 41 auf 62 Mutationen (Mutationen M42 bis M62 für die Nachbesserung)
- **Aufgabe:** Belegen, dass die Tests der Nachbesserung (Rahmennummer, Abschlussmarke, Regelsätze, „leer = fehlt“, Symlink, BOM,
  Doppelabschnitt, Vorspann) die Änderungen wirklich festhalten.
- **Verhalten des Agenten:** Zu den 41 bisherigen Mutationen kamen M42 bis M62 (je eine Stelle in `draft.py`, `limits.py`,
  `draft_prompt.py` oder `policies.py`, der erwartete Test steht als Kommentar im Skript). Vier der alten Mutationen mussten neu
  verankert werden, weil die Stellen im Code ersetzt wurden. Das Skript bricht bei einem nicht gefundenen oder mehrfach vorkommenden Anker
  vor dem ersten Testlauf ab (Meldung „Anker nicht gefunden: Mxx“).
- **Fehler:** Kein Fehler im Paketcode. Befund zum Vorgehen: siehe den Eintrag zur Endlosschleife (M51).
- **Entdeckung:** Läufe des Menschen. Nach Angabe des Menschen sind alle 62 Mutationen rot. Die zwei Symlink-Tests brauchen unter
  Windows den Entwicklermodus, sonst werden sie übersprungen und die zugehörigen Mutationen überleben.
- **Korrektur:** Keine nötig.
- **Konsequenz:** Wer `policies.py` (Symlink-Prüfung) ändert, lässt die Tests mit Entwicklermodus laufen.

### 2026-10-13 · F09 · Befunde der Reviews zu Resources und Prompt (Befunde von security-reviewer / code-reviewer)
- **Aufgabe:** Review von F09 vor dem Pull Request: zwei Agenten ohne Kenntnis der Arbeit, ein Sicherheits-Review und ein Code-Review. Die
  Befunde bewertete der Mensch, danach wurden sie zuerst als Tests festgehalten (Stufe 1) und dann im Code umgesetzt (Stufe 2).
- **Verhalten des Agenten:** Resources und Prompt waren umgesetzt, die Tests grün. Keine kritischen Befunde. Umgesetzt wurden: die
  Rahmennummer in beiden Nachrichten, die Regelsätze (nur erste Anfangs- und letzte Endmarke mit dieser Nummer, Zeilen, die wie
  Markierungen aussehen, sind Daten, der Block KUNDE ist keine Quelle und keine Zusage), eine eigene Abschlussmarke `<<<ENDE-DATEN-n>>>`
  als vierter Markentyp, der Lieferzeit-Satz ohne Einheit, „leer = fehlt“ für `kunde` und `belegte_angaben` (leere Pflichtargumente
  bleiben ungültig), Symlinks aus dem Richtlinien-Ordner heraus werden abgelehnt (innerhalb erlaubt), eine BOM wird akzeptiert, eine
  unbekannte Sprache in `signature_section` ist `PolicyError`, ein doppelter `##`-Abschnitt in `signatur.md` ist ein Fehler, der
  Vorspann von `policy://rabatte` nennt zusätzlich „gerundet, umschrieben oder als Spanne“. Nicht umgesetzt: die doppelten Fixtures der
  Tests zusammenlegen, eine Fabrik statt `fn=build_messages`, ein Helfer für die Ordnerauflösung, ein Test für Threads.
- **Fehler:** Die Befunde betreffen eine Rahmung, die n nicht nannte und deren Schlusssatz im Kundentext nachbildbar war, ein Prompt, der
  `kunde=""` als Fehler behandelte, einen Lader ohne Prüfung von Symlinks, BOM und doppelten Abschnitten, einen Test mit schwacher
  Zusicherung (Nicht-Text-Argument: „Status >= 400“ statt genau 200 und Code −32602), doppelte Tests und veraltete Köpfe („Rot, bis …“).
  Dazu kam ein Widerspruch im Prompt (Einheit der Lieferzeit: der Satz in `draft.py` sagte „Werktagen“, der Katalogtext „in Tagen“).
- **Entdeckung:** Reviews durch die zwei Agenten (mit Datei, Zeile, Schwere, Beleg und Testvorschlag); die Bewertung des Menschen.
- **Korrektur:** Siehe oben; ADR 0006 und die Doku sind nachgeführt. Der Lieferzeit-Satz nennt keine Einheit, `docs/DATENMODELL.md`
  legt Werktage fest. Offen: `server.py:96` sagt „in Tagen“.
- **Konsequenz:** Kundentext-Rahmen immer mit der geltenden Nummer in beiden Nachrichten und mit einer eigenen, nummerierten
  Abschlussmarke. Optionale Argumente: Verhalten bei leerem Wert festlegen und testen. Dateien, die der Dienst liest, auf Symlinks, BOM
  und doppelte Abschnitte prüfen.

### 2026-10-13 · F09 · Mutationsprüfung von Resources und Prompt: zwei Mutationen überlebten, ein Test war zu schwach (Test von Claude Code, Mutation M14 vom KI-Assistenten im Chat)
- **Aufgabe:** Belegen, dass die Tests die Schutzmechanismen von F09 festhalten. 41 Mutationen: Ein Skript des Menschen (außerhalb des
  Repositories) wandte jede einzeln an, führte die zugehörigen Tests aus und stellte die Datei danach zurück.
- **Verhalten des Agenten:** Im ersten Lauf (39 Mutationen) wurden 37 von den Tests bemerkt, 2 überlebten. M13 („der Start-Check druckt
  bei Erfolg“): Der Test `test_start_with_the_real_policy_files_serves_and_prints_nothing_about_them` prüfte nur, dass `uvicorn.run`
  einmal aufgerufen wird und dass in stdout und stderr das Wort „richtlinie“ nicht vorkommt; ein `print` mit anderem Text blieb
  unbemerkt. M14 („die Resource ruft die Datenbank“): Die Mutation des KI-Assistenten im Chat rief `connection()` nur auf, ohne den
  Kontext zu betreten, und löste daher keinen Zugriff aus. Danach kamen M40 und M41 dazu (Resource und Prompt am Schreib-Server), die
  von je drei Tests bemerkt wurden.
- **Fehler:** Zu M13: Der Test war zu schwach. Er prüfte ein Stichwort statt der beiden Kanäle. Zu M14: Die Mutation war zu schwach,
  nicht der Test.
- **Entdeckung:** Läufe des Menschen (Skript über alle Mutationen).
- **Korrektur:** M13: Der Test lässt `main()` bis `uvicorn.run` durchlaufen und prüft stdout und stderr auf exakt leer (`out == ""`
  und `err == ""`); danach war die Mutation rot. M14: Die stärkere Mutation betritt den Kontext; sie machte
  `test_resources_never_touch_the_database` rot. Am Ende waren alle 41 Mutationen rot.
- **Konsequenz:** Ein Test auf „gibt nichts aus“ prüft beide Kanäle exakt, nicht ein Stichwort. Eine Mutation muss den Pfad wirklich
  ausführen (den Kontext betreten), sonst belegt sie nichts.

### 2026-10-13 · F09 · Nicht-Text-Argument als `xfail(strict=True)` angelegt, das SDK spiegelt es nicht (Test von Claude Code)
- **Aufgabe:** Tests für Resources und Prompt `antwort_entwurf` schreiben (Etappe 1), darunter die bekannten Grenzen des SDK beim
  Spiegeln von Eingabewerten.
- **Verhalten des Agenten:** Der Test mit einem Nicht-Text-Argument im Prompt war als `xfail(strict=True)` angelegt, in der Annahme,
  das SDK spiegele den Wert.
- **Fehler:** Die Annahme kam aus dem Lesen des SDK-Quelltexts und war nicht mit einem Test belegt, obwohl `mcp-server/CLAUDE.md` das
  verlangt („Verhalten des SDKs nicht aus einer Stelle im Quelltext folgern, sondern mit einem Test belegen“). Das Protokoll weist ein
  Nicht-Text-Argument schon vorher ab (`arguments` ist `dict[str, str]`, Fehlercode −32602, ohne Wert und ohne Log-Eintrag); der Test
  war deshalb ein XPASS und wegen `strict=True` rot.
- **Entdeckung:** Lauf des Menschen: der Test schlug als XPASS fehl.
- **Korrektur:** Der Test ist ein normaler Test ohne `xfail` (`test_mcp_prompt.py`, `test_mcp_policy_no_leak.py`). Als `xfail`
  bleiben nur die zwei Fälle, die ein Test belegt hat (unbekannte URI und unbekannter Prompt-Name).
- **Konsequenz:** Eine Grenze des SDK erst als bekannte Grenze oder `xfail` festhalten, wenn ein Lauf sie belegt hat; vorher ein
  gewöhnlicher Test, dessen Ergebnis entscheidet.

### 2026-10-13 · F09 · Fehlalarm: Vermutung zum fehlenden Pflichtargument des Prompts (Vermutung vom KI-Assistenten im Chat)
- **Aufgabe:** Das Verhalten des SDK beim Prompt `antwort_entwurf` klären, nachdem ein Log mit einer Pydantic-Kette aufgefallen war.
- **Verhalten des Agenten:** Der KI-Assistent im Chat (nicht Claude Code) vermutete, ein fehlendes Pflichtargument schreibe die
  übrigen Argumente ins Log.
- **Fehler:** Die Vermutung stimmte nicht. Bei einem fehlenden Pflichtargument nennt die SDK-Meldung nur den Namen des Arguments,
  nicht den Wert. Das Problem dort ist ein anderes: Code 0 und ein eigener SDK-Text statt einer festen Meldung.
- **Entdeckung:** Claude Code las die Quelle des SDK und fand dabei das echte Leck: Ein unbekanntes Zusatzargument lässt die
  Pydantic-Prüfung scheitern, und die Ursachenkette mit dem Eingabewert steht im ERROR-Log.
- **Korrektur:** Der Prompt ist eine Unterklasse von `Prompt` (`DraftPrompt`), die die Argumente selbst prüft und nur `MCPError` mit
  fester Meldung wirft. Die Begründung in ADR 0006 nennt nur die belegten Gründe: Code 0 mit SDK-Text, `required` im Listing und die
  Pydantic-Kette mit dem Wert beim unbekannten Argument. Tests: `test_mcp_prompt.py`, `test_mcp_policy_no_leak.py`.
- **Konsequenz:** Eine Vermutung zum SDK wird vor dem Umbau belegt (Lesen der Quelle, dann ein Test), nicht übernommen.

### 2026-10-13 · F09 · Test auf den Dateinamen in der Fehlermeldung suchte zu weit, und „vollständig grün“ gemeldet (Test von Claude Code)
- **Aufgabe:** Test, dass bei einer fehlenden Richtliniendatei nur die Resources die feste Meldung liefern, die die Datei brauchen
  (`test_a_missing_file_gives_the_fixed_message_only_for_the_resources_that_need_it`), und dass dabei kein Pfad und kein Dateiname in
  Antwort, Log, stdout oder stderr stehen.
- **Verhalten des Agenten:** Der Test rief mehrere Resources nacheinander auf und suchte Pfad, Ordner und Dateinamen im gesamten
  gesammelten Ergebnis: auch in den Antworten der Resources, die erfolgreich lieferten, und in Log-Einträgen früherer Aufrufe. Nach
  Etappe 2a meldete ich die Erwartung, `tests/mcp_server` sei vollständig grün.
- **Fehler:** Der Suchbereich war zu weit. Ein Treffer in einem legitimen Text einer erfolgreichen Resource oder in einem früheren
  Aufruf konnte den Test rot machen, ohne dass eine Fehlermeldung etwas verriet. Die Erwartung „vollständig grün“ hatte ich nicht
  gegen diesen Test geprüft, der Fehler lag aber im Test, nicht im Paket.
- **Entdeckung:** Lauf des Menschen nach Etappe 2a (der Test war rot).
- **Korrektur:** Mit ausdrücklicher Freigabe des Menschen: Jede fehlerhafte Resource wird einzeln aufgerufen, davor `caplog.clear()`
  und `capsys.readouterr()`; geprüft werden nur deren Fehlerantwort, Log und Ausgabe auf Pfad, Ordner und Dateiname. Die übrigen
  Resources werden nur auf ihren erwarteten Text geprüft, bei fehlender `signatur.md` liefern beide Resources erfolgreich. Die
  Mutation „Pfad in die Meldung“ macht den Test weiter rot.
- **Konsequenz:** Ein Test auf „steht nirgends“ sucht nur im Ergebnis des einen geprüften Aufrufs, mit zuvor geleertem Log. Vor einer
  Aussage „grün“ den Lauf abwarten; erwartet wird nur, was ein Lauf gezeigt hat.

### 2026-10-12 · F08 · Mutationsprüfung von create_lead: alle Mutationen bemerkt, eine Bedingung hängt an einem einzigen Test (Test von Claude Code)
- **Aufgabe:** Belegen, dass die Tests die Schutzmechanismen von `create_lead` festhalten. Sechs Mutationen, je eine zur Zeit: Der
  Mensch führte die genannten Tests aus, danach wurde die Mutation zurückgenommen.
- **Verhalten des Agenten:** Mutiert wurden (1) der Konflikt bei einem Treffer über den Firmennamen allein entfernt, (2) auf „der
  Name trifft irgendeinen Kunden“ abgeschwächt, (3) der Konflikt bei einer Adresse eines fremden Kunden entfernt, (4) die
  Wiederholung bei jedem `ToolError` statt nur bei `DUPLICATE_ERROR`, (5) eine zweite Wiederholung, (6) die Nachschlagung wieder in
  drei Anweisungen aufgelöst.
- **Fehler:** Kein Fehler im Code. Befund zur Testabdeckung: Die Bedingung „Adresse gehört einem anderen Kunden“ hält nur ein
  einziger Test fest (`test_a_conflict_gives_the_fixed_message_and_writes_nothing`, die Fälle `adresse-fremd-kunde-bestehend` und
  `adresse-fremd-kunde-neu`). Ohne die Bedingung fängt die Datenbank den Fehler über `contacts_email_key` ab, nur mit der Meldung
  `DUPLICATE_ERROR` statt `LEAD_CONFLICT`.
- **Entdeckung:** Läufe des Menschen. Alle sechs Mutationen bemerkten die Tests, jedes Mal aus dem erwarteten Grund. Die Aufspaltung
  der Nachschlagung (6) bemerkten die Rennen-Tests in 20 von 20 Läufen (Angabe des Menschen).
- **Korrektur:** Keine nötig, alle Mutationen sind zurückgenommen.
- **Konsequenz:** Die Rennen-Tests bleiben probabilistisch: Wer die Nachschlagung ändert, lässt sie mehrfach laufen. Die Bedingung
  der Adresse nicht lockern, ohne den einen Test anzupassen.

### 2026-10-12 · F08 · DeadlockDetected im Gesamtlauf: Autovacuum auf den Tabellen des Testschemas (Test und Fixture von Claude Code)
- **Aufgabe:** Tests der Schreibwerkzeuge (Etappe 4b und 4c) mit echter Schreibrolle und echten Commits.
- **Verhalten des Agenten:** Die Rennen-Tests von `create_lead` schreiben und löschen committete Zeilen in `customers` und
  `contacts` (dazu zurückgerollte INSERTs der Verlierer). Danach scheiterte im Paar `test_create_lead.py` und
  `test_search_products.py` etwa jeder dritte Lauf (Angabe des Menschen) beim Setup eines Tests von `test_search_products.py` mit
  `DeadlockDetected` (AccessExclusiveLock gegen ShareLock auf eine Transaktion).
- **Fehler:** Vermutete Ursache: Autovacuum und Autoanalyse liefen im Testschema. Ein Server-Arbeiter hielt auf einer Tabelle seine
  Sperre und wollte `pg_class` fortschreiben; das `ro`-Skript (`GRANT SELECT`, dann `DROP POLICY IF EXISTS`) hielt zur selben Zeit
  die geänderten Tupel und wollte die Tabellensperre: ein Zyklus. Er trat nur auf, wenn mehrere Rennen-Tests nacheinander liefen
  (Angabe des Menschen: einzeln nie); die Erklärung dafür, dass erst die Summe der toten Tupel die Schwelle des Autovacuum
  überschreitet, ist eine Vermutung. Der Ablauf ist aus der Fehlermeldung hergeleitet, das Server-Log mit den beteiligten Befehlen
  wurde nicht ausgewertet; belegt ist die Wirkung des Fixes.
- **Entdeckung:** Gesamtlauf des Menschen. Meine erste Suche galt den Rennen-Tests (Threads, Verbindungen, Aufräumen) und fand dort
  kein Leck. Die Hypothese Autovacuum bestätigte der Mensch mit zwölf Läufen ohne Autovacuum (`ALTER SYSTEM SET autovacuum = off`),
  alle sauber; ohne die Abschaltung scheiterte etwa jeder dritte Lauf.
- **Korrektur:** `tests/mcp_server/conftest.py`, Fixture `db_schema`: Für alle Tabellen des Schemas `test_<hex>` (aus `pg_tables`,
  nicht aus einer festen Liste) `ALTER TABLE … SET (autovacuum_enabled = false)`. Das Anwendungsschema und die Skripte bleiben
  unberührt.
- **Konsequenz:** Tests, die committete Zeilen schreiben und Rollen-Skripte im selben Schema ausführen, brauchen im Testschema kein
  Autovacuum (`mcp-server/CLAUDE.md`). Ob Issue #25 („Deadlock im Test-Setup“, F07) dieselbe Ursache hat, ist nicht geprüft.

### 2026-10-12 · F08 · create_lead: drei getrennte Nachschlagungen ergaben einen falschen Konflikt (Code von Claude Code)
- **Aufgabe:** `create_lead` mit Dublettenschutz umsetzen (Etappe 4c).
- **Verhalten des Agenten:** `attempt()` las den Kunden über die Domain, den Kunden über den Firmennamen und den Eigentümer der
  Adresse mit drei getrennten SELECTs (`find_customer_id`, `find_customer_id_by_name`, `find_contact_owner`). Im Plan stand sogar,
  drei Abfragen je Versuch kosteten nichts Messbares.
- **Fehler:** Unter READ COMMITTED sieht jede Anweisung den Stand zu ihrem eigenen Zeitpunkt. Committete ein paralleler Aufruf
  Kunde und Kontakt zwischen der Domain- und der Namensabfrage, ergab sich „kein Treffer über die Domain, Treffer über den Namen“
  und damit `LEAD_CONFLICT`, obwohl kein Konflikt vorlag.
- **Entdeckung:** Zwei der drei Rennen-Tests (`test_eight_concurrent_calls_for_the_same_new_lead_…` und
  `test_eight_concurrent_calls_with_different_contacts_…`) scheiterten im Lauf des Menschen mit `LEAD_CONFLICT`. Die Tests waren
  für genau das geschrieben; der Fehler stand im Code, nicht im Test.
- **Korrektur:** Eine Anweisung mit drei skalaren Unterabfragen (`FIND_LEAD_MATCHES_SQL`, `db.find_lead_matches`); die zwei nun
  ungenutzten Funktionen sind entfernt. ADR 0005, Punkt 5a, nennt die Gründe und die verworfenen Alternativen (REPEATABLE READ im
  gemeinsamen Verbindungsmanager, Advisory-Lock je Domain). Die Mutationsprüfung (Eintrag oben) belegt, dass die Tests die
  Aufspaltung bemerken.
- **Konsequenz:** Neue Regel in `mcp-server/CLAUDE.md`: Nachschlagungen, deren Ergebnisse gemeinsam über einen Konflikt entscheiden,
  laufen in einer SQL-Anweisung. Bei Plänen mit mehreren Abfragen vor einer Entscheidung die Isolationsstufe ausdrücklich prüfen.

### 2026-10-13 · F08 · Befunde der Reviewer zur Schreibrolle und zum Schreibweg (Code, Test und Doku von Claude Code)
- **Aufgabe:** Review von F08 vor dem Herausnehmen von PR #28 aus dem Entwurf: zwei unabhängige Reviews, ein Code-Review und ein
  Sicherheits-Review, je ein Agent ohne Kenntnis der Arbeit. Die Befunde wurden einzeln eingeordnet, nicht pauschal übernommen.
- **Verhalten des Agenten:** Der Schreibweg war mit `create_lead`, `log_activity`, Schreibrolle und Startprüfung umgesetzt, die Tests
  grün. Eingeordnet wurden die Befunde so:
  - Bestätigt und behoben:
    - Eine Zusicherung in `test_create_lead.py` zu `created_at` war immer wahr, weil die Spalte in den INSERT-Texten gar nicht vorkommt.
      Sie wurde durch eine ersetzt, die rot werden kann.
    - Die Schreibrolle hatte `SELECT` auf `activities`, obwohl der Schreibweg die Tabelle nie liest (INSERT ohne `RETURNING`). Das
      Recht ist entzogen, die Startprüfung meldet es jetzt (`lesen-unerlaubt:activities`).
    - Die Startprüfung der Schreibrolle prüfte nicht, ob Row Level Security auf den drei Schreibtabellen aktiv ist. Ergänzt
      (`rls-aus:<tabelle>`).
    - Dokumentation und Docstrings beschrieben die Startprüfung und den Schreibweg zu weit oder falsch („nie committet“, „gar kein
      Recht“, „wird zurückgegeben“). Korrigiert, bekannte Grenzen in ADR 0005 ergänzt.
    - Der Abbruchpfad (`KeyboardInterrupt`, `BaseException`) in der Schreibverbindung war nicht getestet. Tests ergänzt, zwei
      Mutationen belegen sie.
    - Der lokale Teil der E-Mail-Adresse wird wie die Domain auf ASCII beschränkt: `lower()` in Python und in PostgreSQL weicht bei
      einigen Zeichen ab (Kelvin-Zeichen, İ, ẞ), und das von PostgreSQL hängt von der Locale ab.
  - Widerlegt durch Messung: Das Sicherheits-Review behauptete, die CHECK-Regel `email = lower(email)` schlage für mit Python
    kleingeschriebene Werte an. Das stimmte nicht; auf der lokalen Datenbank war die Regel für alle geprüften Zeichen erfüllt
    (Messung des Menschen). Bestätigt wurde nur die Abhängigkeit von der Locale, die das Code-Review benannt hatte.
  - Zurückgestellt als Issues #29 bis #34 und #36: Test-Helfer, Grenzen des Schreibwegs, Lücken des SQL-Regeltests, fester Rollenname,
    Unicode, SDK-Log von Host und Origin, Wortlaut der Policies. Issue #25 (Deadlock im Test-Setup) bekam einen Kommentar zur möglichen
    gleichen Ursache wie beim Deadlock in F08 (Eintrag oben).
- **Fehler:** Die Befunde betreffen eine Zusicherung, die nichts prüfte, ein Recht, das die Rolle nicht brauchte, eine fehlende Prüfung
  in der Startprüfung, Doku und Docstrings, die mehr behaupteten, als belegt war, einen ungetesteten Abbruchpfad und eine Eingaberegel,
  die zwischen Python und PostgreSQL auseinanderlaufen konnte. Beim Schreiben der roten Tests zur Härtung kam ein eigener Fehler dazu:
  Ein Test (erneutes Ausführen des Rollenskripts nach altem Stand) scheiterte zunächst an seiner eigenen Vorbereitung (die Policy
  existierte schon, `DuplicateObject`) und war damit aus dem falschen Grund rot.
- **Entdeckung:** Reviews durch die zwei Agenten vor dem Herausnehmen aus dem Entwurf; die Widerlegung durch eine Messung des Menschen;
  die falsch rote Vorbereitung im Lauf der roten Tests durch den Menschen.
- **Korrektur:** Die Punkte oben, jeweils zuerst als Test, dann die Umsetzung, dann die Doku. Die Vorbereitung des Tests löscht die
  Policy vorher mit `DROP POLICY IF EXISTS`, bevor die Umsetzung kam; seine Zusicherungen blieben.
- **Konsequenz:** Eine behauptete Wirkung wird gemessen, bevor man darauf aufbaut, auch wenn die Quelle ein Review ist. Ein roter Test
  ist erst belegt, wenn er an der Zusicherung scheitert, nicht an einem Fehler im Aufbau. Review-Befunde mit Issue-Nummer festhalten.

### 2026-10-09 · F07 · Rollen-Skript nur als Superuser getestet, auf Supabase abgebrochen (Code und Test von Claude Code)
- **Aufgabe:** Rollen-Skript `db/roles/data_service_ro.sql` und seine Tests schreiben (Etappe 1), dazu die Anleitung zum Einspielen
  (Supabase SQL-Editor oder `psql`).
- **Verhalten des Agenten:** Claude Code schrieb `ALTER ROLE ... LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION NOBYPASSRLS`
  und führte das Skript in allen Tests mit dem Testbenutzer aus, in der CI also als Superuser `postgres`. Die Anleitung nannte den
  Supabase SQL-Editor, ohne zu klären, mit welchem Benutzer das Skript dort läuft.
- **Fehler:** Das Skript bricht im Supabase SQL-Editor ab: „permission denied to alter role ... Only roles with the SUPERUSER attribute may
  alter roles with the SUPERUSER attribute“. Der Editor läuft nicht als Superuser, und neuere PostgreSQL-Versionen verbieten einem
  Nicht-Superuser schon die Nennung der Option, auch als `NOSUPERUSER`. Der Ausführungsweg eines Nicht-Superusers war nie getestet.
- **Entdeckung:** Probelauf gegen Supabase durch den Menschen (Etappe 9). Ohne `NOSUPERUSER` lief das Skript fehlerfrei durch.
- **Korrektur:** Zuerst die Tests, rot gelaufen: ein Test auf den Skripttext (keine Nennung von `SUPERUSER`) und ein Test, der das
  Skript zweimal als Hilfsrolle ohne Superuser ausführt (`CREATEROLE`, `CREATEDB`, `REPLICATION`, `BYPASSRLS`, Eigentümer von Schema
  und Tabellen). Der zweite scheiterte mit `InsufficientPrivilege` („Nur Rollen mit dem SUPERUSER-Attribut können das
  SUPERUSER-Attribut ändern“). Danach `NOSUPERUSER` aus dem Skript entfernt, mit Kommentar; 938 Tests grün, `ruff format` und
  `ruff check` bestanden. Die Anleitung nennt jetzt die Besonderheiten von Supabase (Session Pooler, Benutzer mit Projektkennung,
  Passwort, Snippet löschen).
- **Konsequenz:** Skripte, die der Mensch auf einer gehosteten Datenbank ausführt, mit einer Rolle testen, die dem dortigen Benutzer
  entspricht, nicht nur als Superuser. „Kein Superuser“ sichern jetzt der Standard von `CREATE ROLE` und die Startprüfung, nicht mehr
  das Skript.

### 2026-10-09 · F07 · Befunde der Reviewer zum Datenservice (Code von Claude Code)
- **Aufgabe:** Review von F07 vor dem Pull Request mit code-reviewer und security-reviewer (Branch `feat/f07-read-tools`; Diff von
  `mcp-server`, `db/roles`, `.github`, `.env.example`, die Tests als Dateiliste).
- **Verhalten des Agenten:** Der Datenservice war mit drei Lesewerkzeugen, Nur-Lese-Rolle, Startprüfung und CI-Job umgesetzt, 936 Tests
  grün (CI). Beide Reviewer fanden keine kritischen Befunde. Zuordnung der Befunde:
  - security-reviewer:
    - W1 SDK-Echo bei fehlendem Pflichtargument: Der Reviewer nannte W1 als Voraussetzung für die Freigabe. Stattdessen dokumentiert
      (ADR 0004) und als Issue #21 erfasst, die Lösung ist offen.
    - H1 Startprüfung deckt nur fünf Tabellen ab, H2 fehlende SELECT-Policy wird nicht erkannt: #22.
    - H3 `sslmode=require` ohne Zertifikatsprüfung (auch als Satz im ADR), H4 keine Begrenzung gleichzeitiger Verbindungen, H6
      Actions nur per Tag gepinnt: #23.
    - H5 gespeicherte Texte unmarkiert, bis zu 20 Kontakte bei `find_customer`: #24.
    - H6 (Teil Wegwerf-Passwort und `-rs`) und H7 neue Abhängigkeiten: keine Aktion, beide Versionen wurden in der CI installiert,
      936 Tests grün.
  - code-reviewer:
    - Wichtig 1 Übergabedatei `F07-stand.md` im Diff: erledigt mit dem Löschcommit, kein Issue.
    - Wichtig 2 drei Commit-Nachrichten ohne Suffix (F07): Verlauf nicht umgeschrieben, bei Squash zählt der PR-Titel, kein Issue.
    - Hinweis 1 veralteter Kommentar in `server.py`: behoben (`bf1a425`).
    - Hinweis 2 Worker-Thread-Kommentar ohne Test: Kommentar abgeschwächt (`bf1a425`), Test nicht ergänzt.
    - Hinweis 3 `dburl.py` als Kopie von `db/seed/guard.py`: ADR-Satz ergänzt (`bf1a425`).
    - Hinweis 4 Wegwerf-Passwort in `ci.yml`: keine Aktion.
    - Hinweis 5 Etappe 9 vor dem Merge: #27.
    - Rückfrage zur Protokollierung mit Lauf-ID, Tokens und Kosten: liegt beim Orchestrator (`agent/`), nicht beim Datenservice, keine
      Aktion.
- **Fehler:** Die Befunde betreffen Doku und Kommentare, die mehr behaupteten, als belegt war (Worker-Thread, UND-Verknüpfung der
  Suche), eine bekannte Lücke im SDK (W1) und Grenzen der Startprüfung, die zum Teil schon im ADR standen.
- **Entdeckung:** Reviews durch die Subagents code-reviewer und security-reviewer vor dem Pull Request.
- **Korrektur:** Folgecommit `bf1a425` für die Kommentar- und Doku-Befunde. Die übrigen Punkte sind Issues: #19 (Seed soll das
  Rollen-Skript anwenden), #20 (`is_active` in den Verweisen von `get_product`), #25 (Deadlock im Test-Setup), #26 (F08-Vormerkungen),
  #27 (Probelauf gegen Supabase).
- **Konsequenz:** Review-Befunde mit Issue-Nummer festhalten. Vor dem Pull Request prüfen, welche Befunde ein Reviewer als Voraussetzung
  für die Freigabe nennt (hier W1).

### 2026-10-09 · F07 · „kein-select:“ im f-String gilt für die Paketregel als SQL (Code von Claude Code)
- **Aufgabe:** Startprüfung der Datenbankrolle in `db.py` umsetzen (Etappe 7, Schritt I). Die Kurzbezeichnungen
  `schreibrecht:…`, `kein-select:…` und `tabelle-fehlt:…` sollten aus festen Teilen und dem Tabellennamen entstehen.
- **Verhalten des Agenten:** Claude Code schrieb `f"kein-select:{row['table_name']}"` und nahm an, die Paketregel in
  `test_package_sql_rules.py` melde nur zusammengesetzte SQL-Sätze.
- **Fehler:** Die Heuristik `SQL_LIKE` (`\bSELECT\b`, ohne Beachtung der Schreibung) hält jede Zeichenkette mit dem Wort
  `select` für SQL, auch den festen Teil `kein-select:` (der Bindestrich ist eine Wortgrenze). Der f-String wurde als „SQL
  durch Einsetzen gebaut“ gemeldet. Die Regel selbst war richtig, die Annahme falsch.
- **Entdeckung:** `test_package_never_puts_values_into_sql_text` im Lauf des Menschen.
- **Korrektur:** Das Dict `NO_SELECT_FAILURES` mit fünf festen Literalen (Tabellenname → volle Kurzbezeichnung); die Regel blieb
  unverändert.
- **Konsequenz:** Geplante Zeichenketten vor dem Schreiben gegen die Regeln der Paket-Tests durchspielen (verbotene Wörter,
  `select`, `SET LOCAL`). Kurzbezeichnungen mit solchen Wörtern als feste Literale schreiben, nie zusammensetzen.

### 2026-10-09 · F07 · Widerspruch zwischen zwei eigenen Testgruppen (Test von Claude Code)
- **Aufgabe:** Tests für die Startprüfung der Datenbankrolle schreiben (Etappe 7, Schritt T).
- **Verhalten des Agenten:** Der Vertrag in `test_role_check.py` verlangte die Kurzbezeichnung `create-im-schema`, und die Prüfung
  braucht den Rechtenamen `CREATE`. Die Paketregel `test_package_sql_rules.py` (von Claude Code in Etappe 3 geschrieben) erlaubte
  in `db.py` nur die exakten Zeichenketten INSERT, UPDATE, DELETE und TRUNCATE.
- **Fehler:** Die neuen Vertragszeichenketten enthalten das verbotene Wort `create`. Die Implementierung hätte die vorhandene
  Regel verletzen müssen. Beim Schreiben der Tests fiel das nicht auf.
- **Entdeckung:** Beim Plan für Schritt I (Lesen der Regel), noch vor dem Code.
- **Korrektur:** Rückfrage an den Menschen. Die Regel bekam eine minimale Ausnahme (nur `db.py`, nur exakt gleiche
  Zeichenketten `CREATE` und `create-im-schema`), Commit `dfe3d0e`.
- **Konsequenz:** Neue Vertragszeichenketten schon in Schritt T gegen die vorhandenen Paketregeln prüfen. Die Regeltests ändert der
  Mensch oder Claude Code nur nach ausdrücklicher Freigabe (`mcp-server/CLAUDE.md`).

### 2026-10-09 · F07 · Lint-Funde im eigenen Code (Code und Test von Claude Code)
- **Aufgabe:** Datenbankzugriff (Etappe 4) und Tests der Startprüfung (Etappe 7) schreiben.
- **Verhalten des Agenten:** Claude Code schrieb in `db.py` einen breiten `except Exception` ohne `# noqa: BLE001` und in
  `test_role_check.py` `__enter__` mit dem Rückgabetyp `"_RecordingCursor"` statt `Self`.
- **Fehler:** Zwei Verstöße gegen `ruff check` (BLE001 und PYI034). Der breite `except` ist Absicht (auch Unerwartetes darf keinen
  Text nach außen tragen), gehört aber mit Begründung markiert.
- **Entdeckung:** `ruff check` im Lauf des Menschen.
- **Korrektur:** `noqa` mit Begründung, `-> Self`.
- **Konsequenz:** Die CI führt jetzt `ruff check mcp-server` aus. Eigenen Code vor der Abgabe gegen diese Regeln prüfen.

### 2026-10-09 · F07 · Fehler beim Schreiben der Tests und des ersten Entwurfs, selbst bemerkt (Code und Test von Claude Code)
- **Aufgabe:** Tests der Lesewerkzeuge (Etappe 3) und `catalog.py` (Etappe 4) schreiben.
- **Verhalten des Agenten:** Beim Gegenlesen vor der Abgabe fielen vier Fehler auf:
  - Der Test auf das `outputSchema` der Lesewerkzeuge wäre vermutlich schon mit den Platzhaltern grün gewesen: Mit dem
    Rückgabetyp `str` baut das SDK ein Schema mit dem Feld `result`, das nicht leer ist.
  - In der Ablehnliste der Artikelnummer stand ein Fall mit angehängtem Zeilentrenner U+2028, den `strip()` entfernt. Die Nummer
    wäre danach gültig gewesen.
  - `@alpha-test.example` stand bei den Nichttreffern von `find_customer`, obwohl der Weg über die Domain trifft.
  - Der erste Entwurf von `catalog.py` baute die Treffer mit `ProductHit(**row)` und einem `type: ignore`.
- **Fehler:** Tests, die vor der Implementierung falsch grün oder dauerhaft falsch rot gewesen wären, und unnötig komplizierter Code.
- **Entdeckung:** Gegenlesen vor der Abgabe, nicht durch einen Lauf.
- **Korrektur:** Der Test prüft die festgelegten Feldnamen, die beiden Fälle sind entfernt, `catalog.py` baut die Treffer Feld für
  Feld.
- **Konsequenz:** Jeden Testfall vor der Abgabe gegen die geplante Regel durchspielen, positiv und negativ.

### 2026-10-09 · F07 · Annahme „geschlossener Loopback-Port wird sofort abgelehnt“ gilt nur für Linux (Test von Claude Code)
- **Aufgabe:** `test_unreachable_database_gives_fixed_error_and_leaks_no_credentials` sollte zeigen: Ist die
  Datenbank nicht erreichbar, gibt der Datenservice die feste Meldung zurück, schreibt genau ein WARNING und
  lässt keine Zugangsdaten in Antwort, Log, stdout und stderr.
- **Verhalten des Agenten:** Claude Code nahm `127.0.0.1:1` als unerreichbare Adresse, schrieb im Kommentar „die
  Verbindung wird sofort abgelehnt“ und prüfte die Fehlerklasse im WARNING mit `[A-Za-z]+Error`.
- **Fehler:** Die Annahme stimmt nur für Linux (`OperationalError`). Unter Windows wird ein geschlossener
  Loopback-Port nicht sofort abgelehnt: psycopg wartet bis `connect_timeout=5` und wirft `ConnectionTimeout`
  („Datenbankfehler: ConnectionTimeout“, Lauf 5,5 s). Die Regex passte nicht, der Test war rot, obwohl der
  Dienst richtig reagierte.
- **Entdeckung:** Grüner Lauf von Etappe 4 durch den Menschen (722 bestanden, 148 fehlgeschlagen): Dieser Test war
  der einzige Fehlschlag außerhalb der erwarteten roten Gruppen.
- **Korrektur:** Regex `[A-Za-z]+(Error|Timeout)`; der Kommentar zu `UNREACHABLE_URL` nennt das Verhalten unter
  Linux und unter Windows. Sonst bleibt der Test unverändert (feste Meldung, genau ein WARNING, keine Zugangsdaten).
- **Konsequenz:** Annahmen zum Verhalten von Netzwerk und Betriebssystem im Test nennen oder plattformunabhängig
  formulieren.

### 2026-10-09 · F07 · Unsichtbare Zeichen wörtlich im Quelltext der Tests (Test von Claude Code)
- **Aufgabe:** `test_limits.py` (Etappe 3) sollte Eingaben mit Steuer-, Format- und Trennzeichen ablehnen, unter
  anderem U+202E (Bidi-Überschreibung), U+200B (Nullbreite-Leerstelle) und U+2028 (Zeilentrenner).
- **Verhalten des Agenten:** Claude Code schrieb die Zeichen als kurze Unicode-Escapes (Backslash, kleines u, vier
  Ziffern) in die Edit-Aufrufe und vermerkte im Quelltext „als Escape, damit sie im Quelltext sichtbar sind“.
  Das Schreibwerkzeug wandelt diese Form beim Speichern in das wörtliche Zeichen um. Claude Code prüfte die
  Datei danach nicht auf wörtliche Zeichen.
- **Fehler:** Die Zeichen standen wörtlich im Quelltext (Trojan-Source-Muster, unlesbar), und der Kommentar
  behauptete das Gegenteil. Betroffen waren `test_limits.py` sowie je ein Testfall in `test_search_products.py`
  und `test_find_customer.py`.
- **Entdeckung:** `ruff check` (PLE2502, PLE2515) beim Lauf durch den Menschen.
- **Korrektur:** Im Scratchpad belegt, dass das Werkzeug die kurze Form umwandelt, die achtstellige Form
  (`\U` mit acht Hexziffern) aber nicht. Alle Stellen auf `\U`-Escapes umgestellt und mit einer Suche über
  `tests/mcp_server` auf die unsichtbaren und Steuerzeichen (U+0000 bis U+0008, U+000B, U+000C, U+000E bis U+001F,
  U+007F bis U+009F, U+00AD, U+0378, U+200B bis U+200F, U+2028 bis U+202E, U+2060 bis U+206F, U+E000 bis U+F8FF,
  U+FEFF) geprüft: kein Treffer.
- **Konsequenz:** Nie unsichtbare oder Steuerzeichen wörtlich in Quelltext schreiben, immer Escapes (achtstellig
  mit `\U`). Nach dem Schreiben die Datei mit einer Suche prüfen, nicht dem eigenen Kommentar glauben.

### 2026-10-08 · F07 · Regex für die DATABASE_URL-Prüfung traf einen eigenen Bezeichner (Test von Claude Code)
- **Aufgabe:** In `test_package_has_no_database_access` sollte das nackte `DATABASE_URL` (Variable des
  Seed-Skripts) im Paket verboten bleiben, `MCP_SERVER_DATABASE_URL` aber erlaubt sein.
- **Verhalten des Agenten:** Claude Code schlug `(?<!MCP_SERVER_)DATABASE_URL` vor und baute es so ein.
- **Fehler:** Die Regex trifft auch den Bezeichner `DATABASE_URL_VAR` in `config.py`: Vor `DATABASE_URL` steht dort
  kein `MCP_SERVER_`. Der Test scheiterte an erlaubtem Code. Dazu war die Meldung zum erwarteten Ergebnis des
  Etappe-2-Laufs („Erwartetes Rot“) widersprüchlich formuliert, und der Config-Test auf `repr`/`str` lief vor der
  Implementierung leer durch, weil er `database_url` nicht vorher prüfte (ein Test, der vor der Implementierung
  grün ist, hätte auffallen müssen).
- **Entdeckung:** Lauf durch den Menschen; die Rot-Erwartung und der leere Durchlauf im Review.
- **Korrektur:** `\bDATABASE_URL\b` statt des Lookbehinds (der Unterstrich gehört zum Wort, daher trifft die Regex
  weder `MCP_SERVER_DATABASE_URL` noch `DATABASE_URL_VAR`); der repr-Test prüft zuerst
  `config.database_url == GOOD_URL`.
- **Konsequenz:** Eine Regex gegen die Namen im Paket prüfen, bevor sie eingebaut wird. Erwartete Ergebnisse eines
  roten Laufs nur nennen, wenn sie einzeln geprüft sind.

### 2026-10-08 · F07 · URL-Tabellen deckten nicht alle Ablehnungsgründe von guard.py ab (Test von Claude Code)
- **Aufgabe:** Paritätstest zwischen `hoffmann_data/dburl.py` und `db/seed/guard.py`: Was das Seed-Skript
  ablehnt, lehnt `dburl` auch ab. Die Tabellen `DBURL_VALID` und `DBURL_INVALID` sollten jeden Ablehnungsgrund
  abdecken.
- **Verhalten des Agenten:** Die Tabellen wurden aus der Beschreibung der Regeln zusammengestellt, ohne jede
  Verzweigung in `guard.py` einzeln gegenzuprüfen.
- **Fehler:** Es fehlten Fälle für: IPv6 ohne schließende Klammer, `?` vor dem `@` im Passwort, Parametername in
  Großbuchstaben (`HOST=`), Datenbankname zu lang, ungültiges UTF-8 im Namen und Schrägstrich im Namen. Bei den
  gültigen URLs fehlten: großgeschriebener Host, prozentkodiertes Passwort, Komma im Passwort, weitere
  Parameter neben `sslmode` und ein Datenbankname mit genau 63 Zeichen. Der Paritätstest hätte dadurch eine
  zu lockere `dburl` nicht bemerkt.
- **Entdeckung:** Review des Chat-Assistenten gegen `guard.py` (Commit `c293e51`).
- **Korrektur:** Elf Fälle ergänzt (sechs ungültige, fünf gültige).
- **Konsequenz:** Bei Paritätstests gegen bestehenden Code jede Verzweigung der Vorlage einzeln in der Tabelle
  abhaken. Der Hinweis stand in der Übergabedatei F07-stand.md, die mit dem Abschluss von F07 entfernt wurde.

### 2026-10-08 · F07 · UPDATE-Testfall prüfte nicht die Rechte (Test von Claude Code)
- **Aufgabe:** `test_role_cannot_write` sollte zeigen, dass die lesende Rolle `data_service_ro` auf den fünf
  Tabellen nicht schreiben darf (`InsufficientPrivilege`).
- **Verhalten des Agenten:** Der UPDATE-Fall lautete `UPDATE {} SET id = id WHERE false`.
- **Fehler:** Die Spalte `id` ist `GENERATED ALWAYS`. PostgreSQL meldet dafür „column id can only be updated to
  DEFAULT“, und zwar vor der Rechteprüfung. Der Test erwartete `InsufficientPrivilege` und konnte so nicht grün
  werden, obwohl die Rolle korrekt eingeschränkt war.
- **Entdeckung:** Review des Chat-Assistenten (Commit `73e4d1b`), gegen PostgreSQL 16 bestätigt: `SET id = DEFAULT
  WHERE false` liefert für alle fünf Tabellen SQLSTATE 42501.
- **Korrektur:** Der Fall lautet `UPDATE {} SET id = DEFAULT WHERE false`, mit einem Kommentar zum Grund.
- **Konsequenz:** Rechtetests auf Identity-Spalten immer mit `SET id = DEFAULT`. Der Hinweis stand
  in der Übergabedatei F07-stand.md, die mit dem Abschluss von F07 entfernt wurde.

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