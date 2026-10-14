# ADR 0007: Fortschrittsmeldungen aus synchronen Werkzeugen, feste Texte, Best-Effort

- Status: Angenommen
- Datum: 2026-10-14

## Kontext
F10 verlangt, dass ein Client bei länger laufenden Aufrufen Zwischenstände erhält (Auftrag 3.2 und 6.4). Das MCP-Protokoll kennt dafür
`notifications/progress` (`progressToken`, `progress`, optional `total` und `message`); das SDK (`mcp==2.3.0`) sendet sie nur, wenn der Client
`_meta.progressToken` mitschickt. Die Werkzeuge des Datenservice sind synchron (psycopg), das SDK ruft sie in einem Worker-Thread auf.
Der Transport ist zustandslos mit Ereignisstrom (ADR 0003). Das SDK bedient zwei Wege je nach Header `MCP-Protocol-Version`: den alten Weg
(ohne Header oder Handshake-Version) und den modernen Weg (`2026-07-28`). Ehrlich vorweg: Heute läuft kein Werkzeug lange (Statement-Limit
5 s, Verbindungs-Limit 5 s). Das Feature dient der Schnittstelle, nicht der gefühlten Dauer.

## Entscheidung
1. **Synchrone Werkzeuge bleiben synchron.** Sie bekommen den Parameter `ctx: Context` (steht in keinem Eingabeschema) und senden über einen
   `ProgressReporter` mit `anyio.from_thread.run(...)` aus dem Worker-Thread. Belegt durch `test_progress_from_thread.py`; der Weg gilt auf
   beiden Wegen des Servers (`test_progress_eras.py`, `test_progress_sdk_client.py`).
2. **Feste Texte.** `progress.py` hat `Step` als `StrEnum`; der Reporter nimmt nur einen `Step`, nie einen Text. Eine Meldung kann so keinen Wert
   (Eingabe, Kunden- oder Katalogdaten, Pfad) enthalten. Texte und Zeitpunkte:
   - `find_customer`, innerhalb der lesenden Verbindung, je nach der Abfrage: „Kunde gesucht“ (auch ohne Treffer, dann endet die Folge),
     „Ansprechpersonen geladen“, „Verlauf geladen“; `total` 3.
   - `create_lead` und `log_activity`, nie innerhalb der Transaktion: „Eingabe geprüft“ nach der Prüfung und vor dem Öffnen der Verbindung,
     „Transaktion abgeschlossen“ nach dem Commit; `total` 2. `create_lead` meldet bei der Wiederholung nach einem verlorenen Rennen
     „Wiederholung nach Konflikt“ nach dem Rollback und vor der zweiten Verbindung. Nach einem Fehler folgt keine Abschlussmeldung.
   - `search_products` und `get_product` melden nicht (je eine Abfrage).
   „Transaktion abgeschlossen“ statt „Eintrag gespeichert“: Bei einem bestehenden Kunden schreibt `create_lead` nichts (Dublettenschutz, ADR 0005),
   der Text muss in beiden Fällen stimmen.
3. **Grenzen.** Höchstens 6 Meldungen je Aufruf. `progress` steigt mit jeder Meldung um 1, auch über die Wiederholung; bei `create_lead` kann
   `progress` darum 3 sein bei `total` 2 (bewusst, selten). Zeitlimit je Meldung `PROGRESS_TIMEOUT_SECONDS = 1,0` (Konstante, keine
   Umgebungsvariable), umgesetzt mit `anyio.fail_after` in der übergebenen Coroutine.
4. **Best-Effort.** Ein Fehler oder das Zeitlimit beim Senden bricht den Aufruf nie ab und ändert sein Ergebnis nicht. Der Reporter schaltet sich
   nach dem ersten Fehler für den Rest des Aufrufs ab (Wartezeit insgesamt höchstens ein Zeitlimit). Das Log nennt nur die Klasse der Ausnahme.
   Eine Ausnahme ist ein Programmierfehler: `step()` wirft ein `TypeError`, wenn kein `Step` übergeben wird (ausserhalb des Best-Effort, kein Sendefehler).
   Bei einer abgebrochenen Aufgabe kommt im Worker-Thread `asyncio.CancelledError` an, eine `BaseException`; deshalb fängt der Reporter sie ausdrücklich
   (`test_progress_cancellation.py`, `test_progress_unit.py`). Ein abgebrochener Host gibt den Thread nicht frei (ebenda), nur das Zeitlimit tut es.
5. **Client-Abbruch.** Geht der Client mitten im Aufruf, läuft das Werkzeug zu Ende (Commit wie ohne Fortschritt). Ein Wiederholungsaufruf trifft
   die Dublettenerkennung aus F08 (`customer_created=false`, kein weiterer `INSERT`); belegt in `test_progress_stream.py`.
6. **`progressToken`.** Er ist ein vom Client geschickter Wert (Zeichenkette oder Zahl) und kommt laut Protokoll in jeder Meldung zurück. Das ist die
   bewusste Ausnahme von „keine Eingabewerte in Antworten“. Er steht nur in diesen Meldungen, nie im Ergebnis, nie im Log unseres Codes (ab INFO), nie in stdout oder stderr
   (`test_progress_security.py`). Bei DEBUG schreibt der Transport (`sse_starlette`) die ganze Meldung ins Log, samt Token (DEBUG nie mit echten Daten). Das Zugangstoken wird vor allem anderen geprüft, auch bei beliebigem Wert des Headers `MCP-Protocol-Version`: ohne
   gültiges Token 401 ohne ein Byte Fortschritt.
   **Begrenzung.** Weil das SDK den Token in jede Meldung zurückschickt, könnte ein Client sonst beliebig große Werte (bis zur Anfragegrenze von 256 KiB, bis zu
   dreimal zurück; gemessen: Eine Anfrage mit 300 KiB, als Token oder als Argument, bekommt auf beiden Wegen HTTP 413 „Request body too large“, `test_progress_token_sources.py`) in unsere Antworten spielen und mit einem Client, der nicht liest, Worker-Thread und Verbindung länger festhalten. `progress_for(ctx, total)`
   (`progress.py`) prüft den Wert, den das SDK zurückschickt, nicht eine zweite Sicht darauf: Es wendet `progress_token_from_params` (dieselbe Funktion, mit der das SDK
   den Token aus den rohen `params["_meta"]["progressToken"]` liest) auf `ctx.request_context.params` an. `ctx.request_context.meta` ist ein von Pydantic gebautes
   Dict und damit eine zweite Sicht, die vom rohen Wert abweicht: Ein zusätzlicher Schlüssel `progress_token` neben `progressToken` im `_meta` des Clients überschreibt dort den Wert (deshalb die Quelle);
   `test_progress_token_sources.py` hält die Fälle mit beiden Schlüsseln (beide Reihenfolgen, beide Wege) fest. Die Funktion des SDKs liefert nur eine Zeichenkette oder eine
   Ganzzahl. `true` und 5.0 sind kein Token: Das Werkzeug läuft, `meta` zeigt die Ganzzahl 1 bzw. 5, das SDK schickt nichts zurück. Zahlen mit Nachkommastellen (1.5), Listen,
   Objekte und `null` lehnt das SDK vor dem Werkzeug ab: `INVALID_PARAMS` (-32602) mit der festen Meldung „Invalid request parameters“ (alter Weg: HTTP 200 mit JSON-RPC-Fehler, moderner Weg:
   HTTP 400); der abgelehnte Wert steht weder in der Antwort noch im Log noch in stdout oder stderr. Das halten Assertions in `test_progress_token_access.py` fest. Eine Ganzzahl über 2^53 − 1 (gemessen: 2^64 + 1) läuft auf beiden
   Wegen normal, ohne Meldung. Ein einzelnes unpaariges Ersatzzeichen (U+D800) verhält sich je Weg anders (Verhalten des SDKs, kein Fehler des Dienstes): Der alte Weg lehnt es beim Parsen ab (HTTP 400,
   JSON-RPC-Fehler -32700, „Parse error“, das Werkzeug läuft nicht, der Wert steht weder in Antwort noch Log), der moderne Weg nimmt es an (HTTP 200, 3 Meldungen, der Token kommt unverändert zurück); in beiden Fällen
   gibt es kein Leck und keinen Abbruch, der Server beantwortet den nächsten Aufruf. Gemeldet wird nur bei einer Zeichenkette mit höchstens `MAX_TOKEN_LENGTH = 64`
   Zeichen (Codepunkte, nicht Bytes; auch leer, so lässt es die Spezifikation zu) oder einer Ganzzahl mit Betrag bis `MAX_TOKEN_INT = 2**53 - 1`. Das sind Betriebsgrenzen, nicht aus der Spezifikation.
   Die Bytegröße je Meldung ist gemessen (rohe Größe des SSE-Ereignisses, Rahmen eingeschlossen, `test_progress_token_sources.py`): größtes Ereignis auf dem alten Weg 551 Byte (64 × U+0001, 6 Byte je Zeichen im JSON),
   auf dem modernen Weg 935 Byte (64 Nicht-BMP-Zeichen, 12 Byte je Zeichen als Ersatzpaar-Escape). Die Obergrenze im Test ist 1024 Byte je Ereignis.
   Alles andere gilt als kein Token: Der Aufruf läuft still ohne Fortschritt, ohne Log-Zeile und ohne Fehlermeldung; der Client sieht am Ausbleiben der Meldungen, dass der Token nicht
   genutzt wurde. Ohne gültigen Token entfällt auch der Thread-Sprung (`test_no_progress_never_hops_into_the_event_loop`). Ein Fehler beim Lesen des Contexts oder der Params bricht den Aufruf nie ab: `NO_PROGRESS`, im Log nur
   die Klasse der Ausnahme. Geprüft in `test_progress_token_limits.py` und `test_progress_token_sources.py`; die Zeichenklassen (Nicht-BMP, Steuerzeichen, Anführungszeichen und Rückstriche, U+2028/U+2029/U+0085/DEL, weitere Steuerzeichen) prüfen strenge Tests auf beiden Wegen (Status 200, genau 3 Meldungen, Token unverändert, ein zweiter Aufruf wird beantwortet, kein Zeichen im Log ab INFO, stdout, stderr). Ein ungültiger Token ändert Ergebnis und Rahmen der Antwort nicht (identisch mit „ohne Token“).
7. **Abhängigkeit.** `anyio` wird direkt importiert und steht deshalb fest gepinnt (`4.15.1`, die bereits installierte Version) in
   `mcp-server/requirements.txt`; das Projekt pinnt, was es direkt importiert.

## Alternativen
- **Werkzeuge `async`, Datenbank je Schritt im Thread:** `create_lead` müsste in Schrittfunktionen zerlegt werden, eine Verbindung wanderte zwischen
  Worker-Threads, Transaktion und Wiederholung (F08) würden angefasst. Großer Diff, Risiko für F08, für einen Gewinn, den Entscheidung 1 billiger hat.
- **Kein Fortschritt:** verfehlt F10.
- **Meldungen mit Werten** („Kunde X gefunden“): verworfen, weil Texte aus Eingaben oder Daten in Strom und Log wandern könnten.
- **Meldung innerhalb der Schreib-Transaktion** („Dublettenprüfung läuft“): verworfen. Ein Client, der nicht liest, könnte die Transaktion bis zum
  Zeitlimit offen halten. Preis: kein Zwischenstand aus der Transaktion.

## Konsequenzen
- **Beide Wege.** Der alte Weg liefert mit Token einen Strom. Der moderne Weg liefert JSON, wenn der Aufruf ohne Ereignis abschließt, und schaltet ab der
  ersten Meldung auf den Strom um (`_SSE_PING_INTERVAL` 15 s hält einen stillen Strom mit Kommentarzeilen offen); belegt in `test_progress_eras.py`.
  `json_response=False` aus ADR 0003 wird damit genutzt.
- **Gegendruck.** Auf dem modernen Weg ist der Strom ohne Puffer: Die erste Meldung wird angenommen, die zweite blockiert den Thread, bis der Client liest
  (`test_progress_backpressure.py`). Das Zeitlimit je Meldung verhindert, dass ein nicht lesender Client eine lesende Verbindung in einem Aufruf länger als dieses Limit
  aufhält (`test_progress_stream.py`). `find_customer` meldet innerhalb der lesenden Verbindung; die Wartezeit je Meldung ist höchstens 1,0 s. Das gilt
  je Aufruf: Wie viele Aufrufe gleichzeitig laufen (Thread-Limiter und Pool des SDKs, Verbindungsgrenze der Datenbank), ist ein Betriebspunkt für F30.
- **`progressToken` begrenzt (Entscheidung 6).** Ein Token außerhalb der Grenzen erzeugt keine Meldung; der Client merkt es nur daran, dass kein Fortschritt kommt.
  Ergebnis und Rahmen der Antwort sind dabei identisch mit „ohne Token“: `test_progress_token_limits.py` vergleicht sie mit einem Referenzwerkzeug ohne F10 (`probe_app`) und
  verlangt, dass ein ungültiger Token den Rahmen genauso wenig ändert wie dort.
- **Ausnahme von „kein Port in Tests“** (`mcp-server/CLAUDE.md`): `test_progress_server.py` und `test_progress_sdk_client.py` starten uvicorn im Thread auf
  `127.0.0.1` (Port 0, Socket vorab gebunden, `SelectorEventLoop`). Ohne Sockets Skip lokal, in GitHub Actions ein Fehler (wie `TEST_DATABASE_URL`).
  Ereignisbasiert, mit hartem Zeitlimit je Wartezeit. Der ASGI-Test ohne Port (`test_progress_stream.py`) bleibt der Hauptbeweis für „vor dem Ergebnis“.
- **Befund Teardown (Testhilfe, Windows):** Unter Windows hing das Beenden des Test-Servers in einem Teil der Läufe (Messreihe: 5 von 20, 15,8 s). Ursache,
  aus der Diagnose und den Quellen von Python 3.13 und uvicorn 0.54.0 belegt: Die `ProactorEventLoop` ruft in `_call_connection_lost` `sock.shutdown()`
  in einem `finally`; ein `ConnectionResetError` überspringt `sock.close()` und `Server._detach`, der asyncio-Server zählt die Verbindung weiter,
  `wait_closed()` in `uvicorn.Server.shutdown` wartet ohne Ende und `lifespan.shutdown` wird nie gesendet. Das ist eine Eigenheit der Testhilfe unter Windows, kein
  Stream-Problem. Die Testhilfe läuft deshalb auf der `SelectorEventLoop`. Erwartung (die CI zeigt es): Der Linux-Runner hat das Problem nicht, dort ist die
  Selector-Schleife Standard.
- **Befund Herunterfahren mit offenem Strom** (aus dem Quelltext von uvicorn 0.54.0, nicht per Test belegt): `Server.shutdown` wartet auf offene Verbindungen
  und Tasks, solange `timeout_graceful_shutdown` nicht gesetzt ist. Ein offener Strom hält das Beenden also an. Die Entscheidung zum Betrieb (Wert, Verhalten bei
  Wartungsneustart) gehört zu F30.
- **`httpx2` in den Port-Tests:** `test_progress_server.py` und `test_progress_sdk_client.py` importieren `httpx2` direkt, es ist nicht gepinnt (es kommt über
  `mcp`). Bewusste Ausnahme von „pinnen, was direkt importiert wird“: Es betrifft nur Tests und das SDK selbst. Bei einem Update von `mcp` zuerst `tests/mcp_server`.
- **Mutationsprüfung:** Sie lief lokal mit einem Skript, das nicht versioniert ist (wie bei F09); die Tests sind der Beleg, nicht das Skript. Stand mit `progress_for` (rohe Params):
  34 Mutationen (M01 bis M34), alle entdeckt, keine überlebt, kein Fehler. 33 davon wurden von fehlschlagenden Tests entdeckt (KILLED). M07 (Zeitlimit je Meldung entfernt) erscheint im
  Gesamtlauf mit `-Alle` als TIMEOUT, weil dort alle hängenden Tests in ihren Wächter laufen und die Summe die 180 Sekunden überschreitet; im Einzellauf ohne `-Alle` ist M07 KILLED, vom Test
  `test_a_hanging_send_is_dropped_after_the_timeout_and_switches_the_reporter_off` (`test_progress_unit.py`).
- **Test geändert:** In `test_create_lead.py` erwartet `test_the_function_has_no_switch_to_turn_the_duplicate_check_off` den Parameter `progress` als letzten
  Parameter von `crm.create_lead`. `progress` ist ein Meldekanal, kein Schalter der Dublettenprüfung; die Absicht des Tests bleibt.
- **Pflichten.** `progress.py` kennt keine Datenbank (AST-Test), neue Texte stehen nur in `Step` und enthalten keine Schlüsselwörter der Paketregel
  (`test_package_sql_rules.py`). Änderungen an `crm.py` ändern weder SQL noch Transaktion noch die Wiederholungsregel von F08.
