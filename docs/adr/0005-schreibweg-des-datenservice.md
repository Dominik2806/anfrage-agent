# ADR 0005: Schreibweg des Datenservice

- Status: Angenommen
- Datum: 2026-10-10
- Nachgeführt: 2026-10-12 (nach der Umsetzung von F08; geändert sind Punkt 5 und die Konsequenzen, ergänzt Punkt 1, 3, 5a, 5b, 6, 7 und 8)

## Kontext
F08 verlangt `create_lead` und `log_activity` (Auftrag 3.2, 4.7, 6.4). Doppelt angelegte Kunden werden verhindert,
ein Test belegt das. `create_lead` gilt „nur nach Freigabe“. Der Agent darf Leads anlegen und Aktivitäten protokollieren,
aber nichts überschreiben oder löschen, und das ist technisch durchzusetzen (Auftrag 5, 5.1). Heute schreibt der
Datenservice nie: Die Rolle `data_service_ro` hat nur `SELECT`, die Verbindung ist schreibgeschützt, und die Startprüfung
lehnt jedes Schreibrecht ab (ADR 0004). Es gibt ein einziges Token (ADR 0003), und eine Freigabe lässt sich in der
Datenbank noch nicht nachprüfen, weil die Tabellen für Anfragen und Freigaben fehlen (DATENMODELL Abschnitt 1 und 2.5).
Der Auftrag legt weder Parameter noch Rückgaben noch Fehlerfälle fest, und das Ergänzen eines bekannten Kunden um einen
neuen Kontakt (4.7) hat kein eigenes Werkzeug.

## Entscheidung
1. **Eigene Schreibrolle.** Der Schreibweg verbindet sich als Rolle `data_service_rw` mit `INSERT` und `SELECT`
   auf `customers`, `contacts` und `activities`, ohne `UPDATE`, `DELETE`, `TRUNCATE`, `BYPASSRLS`, Superuser und
   `CREATE` (je eine `SELECT`- und eine `INSERT`-Policy, die `INSERT`-Policy mit `WITH CHECK`). Eine eigene
   Startprüfung (`verify_write_role`) prüft genau diese Menge: Auf `customers`, `contacts` und `activities` lehnt sie jedes
   weitere Recht ab, auf `products`, `product_fits` und `discount_rules` jedes Recht. Es gibt keinen Schalter. Die Meldungen der Prüfung
   stehen auf stderr mit dem Präfix `Schreibweg:` (Kurzbezeichnungen: `mcp-server/README.md`). ADR 0004
   bleibt unverändert gültig: Die Lese-Werkzeuge nutzen weiter nur `data_service_ro`. Verbindung, URL und Skript
   (`db/roles/data_service_rw.sql`) sind getrennt.
2. **Eigene Konfiguration.** `MCP_SERVER_WRITE_DATABASE_URL` und `MCP_SERVER_WRITE_TOKEN` sind eigene
   Umgebungsvariablen, mit denselben Regeln wie die Lese-Variablen. Beide fehlen: Der Schreibweg ist nicht verfügbar,
   `/mcp-write` antwortet nicht. Nur eine von beiden gesetzt, oder beide Token gleich: Der Start bricht ab.
3. **Zwei Token, zwei Pfade.** `/mcp` (Lese-Werkzeuge, Resources, Prompt) nur mit dem Lese-Token, `/mcp-write`
   (`create_lead`, `log_activity`) nur mit dem Schreib-Token. Zwei `MCPServer`-Instanzen laufen im selben Prozess, je
   in `BearerTokenMiddleware` mit eigenem Token. Das Schreib-Token auf `/mcp` und das Lese-Token auf `/mcp-write`
   werden je mit der einheitlichen 401-Antwort abgelehnt. Die Prüfung der erlaubten Hosts (Host-/Origin-Schutz) gilt
   für beide Apps. Eine Pfadweiche (`hoffmann_data/switch.py`) wählt allein nach dem exakten Pfad (nur `/mcp-write`
   geht an die Schreib-App, alles andere an die Lese-App), liest keinen Body und prüft kein Token.
   Auslegung von F06 („listet seine Schnittstellen auf“): Jeder Endpunkt listet seine eigenen Schnittstellen
   (`/mcp`: drei Lese-Werkzeuge, beide Resources und der Prompt; `/mcp-write`: `create_lead` und `log_activity`). Die
   Vereinigung beider Listen sind die acht Schnittstellen des Auftrags 6.4.
4. **Freigabesperre als Übergang.** Das Schreib-Token hat nur der Freigabeweg, nie der Agent. Der Agent bekommt
   nur das Lese-Token und sieht die Schreib-Werkzeuge nicht. Später prüft der Server eine `approval_id` in der
   Datenbank (F19 bis F21); das ist hier nicht entschieden.
5. **Dublettenabgleich in `create_lead`.** Die Domain kommt aus der E-Mail-Adresse (die ganze Adresse wird kleingeschrieben,
   nur `.example`, Punkt 7). Abgeglichen wird exakt und ohne Beachtung der Schreibung: Kunde über die Domain, Kunde über den
   Firmennamen, Eigentümer der Adresse.
   - **Treffer über die Domain, ohne Widerspruch:** Der bestehende Kunde bleibt unverändert (kein Feld wird überschrieben,
     auch nicht Status, Branche, Land oder Sprache). Eine unbekannte Adresse wird als Kontakt ergänzt, eine bekannte bleibt
     unverändert.
   - **Konflikt** (feste Meldung `LEAD_CONFLICT`, es wird nichts geschrieben): (a) der Firmenname trifft einen Kunden, die
     Domain keinen; (b) Domain und Firmenname treffen verschiedene Kunden; (c) die Adresse gehört einem anderen Kunden als dem
     gefundenen, oder es gibt keinen gefundenen Kunden und die Adresse existiert schon. Alle Konfliktprüfungen laufen vor dem
     ersten INSERT, es wird nie geraten.
   - **Änderung gegenüber dem ersten Text dieser ADR:** Dort lieferte ein Treffer über den Firmennamen allein den bestehenden
     Kunden zurück. Das ist ein Konflikt, weil die Domain des Kontakts zur Domain der Firma passen muss (DATENMODELL, Prüfung 2):
     Ein nachgetragener Kontakt verletzte sie. Im Zweifel eskaliert der Agent.
   - Subdomains werden nicht zusammengeführt: `mail.firma.example` ist ein anderer Kunde als `firma.example`, mit gleichem
     Firmennamen gilt (a).
   - Kein Treffer: Kunde (Status `lead`) und Kontakt entstehen in einer Transaktion.
   - Die UNIQUE-Constraints auf `domain` und `email` und der eindeutige Index auf `lower(company_name)` bleiben die zweite
     Sperre.
   - **5a. Eine Momentaufnahme.** Die drei Nachschlagungen laufen in **einer** SQL-Anweisung (drei skalare Unterabfragen,
     `db.find_lead_matches`). Unter READ COMMITTED sieht jede Anweisung den Stand zu ihrem eigenen Zeitpunkt: Committet ein
     paralleler Aufruf Kunde und Kontakt zwischen der Domain- und der Namensabfrage, entstünde „kein Treffer über die Domain,
     aber über den Namen“ und damit ein falscher `LEAD_CONFLICT` (so geschehen, siehe `AGENT_LOG.md`). Verworfen: REPEATABLE
     READ im gemeinsamen Verbindungsmanager (wirkt auch auf `log_activity` und bringt SQLSTATE 40001); ein Advisory-Lock je
     Domain (schützt den Namenskonflikt zwischen verschiedenen Domains nicht).
   - **5b. Wiederholung nach verlorenem Rennen.** Verliert ein Aufruf ein Rennen, meldet die Datenbank `UniqueViolation`
     (SQLSTATE 23505), und `write_connection` macht daraus `DUPLICATE_ERROR`. `create_lead` wiederholt dann den **gesamten**
     Ablauf (Nachschlagen, Konfliktprüfung, INSERTs) genau **einmal** in einer neuen Transaktion; die Wiederholung findet den
     Konkurrenten und gibt den bestehenden Kunden zurück. Eine zweite Niederlage gibt die feste Meldung weiter, nie eine
     Schleife. Andere Fehler (`DATABASE_ERROR`, `NOT_CONFIGURED`, `LEAD_CONFLICT`, Eingabefehler) werden nicht wiederholt. Jeder
     Fehlversuch schreibt genau eine Warnung ins Log (Klasse und SQLSTATE, nie Werte).
6. **Prüfungen 2, 3 und 3b** (DATENMODELL Abschnitt 5) gelten im Werkzeug: Die Domain der Kontakt-Adresse entspricht der
   Domain der Firma, und `occurred_at` und `created_at` setzt die Datenbank (`now()`, nicht die Uhr des Dienstes). Damit
   liegt nie ein Zeitstempel vor `created_at` der Firma. `created_at` von Kunde und Kontakt kommt aus dem `DEFAULT now()` der
   Tabellen, `occurred_at` aus `now()` im INSERT; `now()` ist der Beginn der Transaktion, Kunde und Kontakt eines Aufrufs
   haben denselben Zeitstempel.
7. **Parameter und Werte.**
   - `create_lead`: Firmenname, Kontakt (Vorname, Nachname, E-Mail-Adresse, Sprache, optional Position), `country`,
     `industry`. Status ist immer `lead`. Annahme: `country` ist Pflicht und wird vom Freigabeweg gesetzt, `industry` hat
     den Standard `other`.
   - `log_activity`: Domain des Kunden (natürlicher Schlüssel, DATENMODELL Abschnitt 4), `type`, `subject`, `summary`, optional
     E-Mail des Kontakts.
   - `type` ist nur `inquiry`, `complaint` oder `note`. Es gibt keinen `amount_eur` und keinen Artikelbezug.
   - `created_by` ist immer `agent`.
   - Ein unbekannter Kunde bei `log_activity` ist ein fester Fehler, es wird nie ein Kunde angelegt.
   - Domains außerhalb `.example` lehnt die Datenbank per CHECK ab. Das Werkzeug prüft das vorher in `limits.py` und
     gibt eine feste Meldung.
   - **Textgrenzen** (`limits.py`, Zeichen, nach Entfernen der Ränder): Firmenname 1 bis 200, Vorname und Nachname je 1 bis
     100, Position höchstens 100 (leer oder fehlend heißt „nicht angegeben“), E-Mail höchstens 254, Domain höchstens 253,
     Betreff 1 bis 200, Zusammenfassung 1 bis 2000. Firmenname, Namen und Position sind einzeilig, ohne Steuer-, Format- und
     Trennzeichen; Betreff und Zusammenfassung erlauben Zeilenumbruch, Wagenrücklauf und Tab. Aufzählungen werden exakt
     verglichen, ohne `strip` und ohne `lower`: Sprache `de` oder `en`, Land `de`, `at` oder `ch`, Branche `automotive`,
     `food`, `packaging`, `metalworking`, `plastics`, `logistics` oder `other` (Standard), Typ `inquiry`, `complaint` oder
     `note`. Die Domain muss reines ASCII sein und wird dann kleingeschrieben, die E-Mail-Adresse wird ganz kleingeschrieben
     (das Schema verlangt `lower`).
   - Bei `log_activity` ist ein Kunde mit Status `inactive` erlaubt. Ein Kontakt wird über Adresse **und** Kunde gefunden;
     „Adresse unbekannt“ und „Adresse gehört zu einem anderen Kunden“ haben dieselbe feste Meldung. `contact_email` als leerer
     Text ist ungültig, nur ein fehlendes Feld heißt „kein Kontakt“.
   - **Ergebnisse ohne interne ID:** `create_lead` gibt `customer_domain`, `customer_created` und `contact_created` zurück,
     `log_activity` gibt `customer_domain`, `type` und `logged` zurück (`logged` ist immer wahr).
8. **Eine Transaktion pro Aufruf.** Eigener Schreib-Verbindungsmanager in `db.py` (nicht schreibgeschützt, Zeitlimit 5 s je
   Abfrage mit `SET LOCAL`, Commit am Ende, bei jedem Fehler Rollback). Eine `UniqueViolation` wird zu `DUPLICATE_ERROR`, jeder
   andere Fehler zu `DATABASE_ERROR`; ein `ToolError` aus dem Block läuft unverändert durch. Feste Fehlermeldungen ohne
   Eingabewerte wie im Lesepfad.

## Alternativen
- **Dieselbe Rolle mit zusätzlichen Rechten:** verworfen. Die Lese-Werkzeuge könnten dann schreiben, die Startprüfung
  und ADR 0004 müssten abgeschwächt werden, und ein Fehler in einer Lese-Abfrage träfe die Daten.
- **Ein Token, ein Pfad:** verworfen. Wer das Token hat, könnte `create_lead` aufrufen, auch der Agent, der fremden
  Kundentext verarbeitet (Auftrag 5.2). Die Sperre wäre nur Konvention.
- **Scope-Prüfung im Werkzeug mit beiden Token an einem Pfad:** Rückfall. Der Agent sähe die Schreib-Werkzeuge in `tools/list`, die Sperre läge im Werkzeugcode, und die Übergabe der Kennung von der Middleware bis ins Werkzeug ist im SDK nicht geprüft.
- **Weiche mit Body-Prüfung (Werkzeugname im JSON-RPC):** verworfen. Das vergrößert die Angriffsfläche und widerspricht
  der Entscheidung nur nach Pfad.
- **Datenbankfunktionen (`SECURITY DEFINER`) statt Schreibrolle auf Tabellen:** nicht gewählt. Sie wären ein größerer
  Eingriff ins Schema und stehen nicht im Material.
- **Schreiben durch die Web-App direkt:** nicht gewählt. 6.4 nennt die Schreib-Werkzeuge als Datenservice-Schnittstellen.

## Konsequenzen
- Der Datenservice hat zwei Rollen, zwei Token und zwei Pfade. Der Mensch spielt `data_service_rw.sql` ein, setzt das
  Passwort getrennt und führt das Skript nach jedem `python -m db.seed` erneut aus (der Reset löscht die Rechte).
  Issue #19 (Seed richtet die Rolle wieder ein) betrifft beide Rollen-Skripte, `data_service_ro.sql` und
  `data_service_rw.sql`.
- Der MCP-Client (F13) braucht zwei Verbindungen. Die Schreib-Verbindung bekommt der Agent nicht.
- Die Sperre „nur nach Freigabe“ ist so stark wie die Trennung der Geheimnisse. Der Server prüft nicht, ob tatsächlich
  freigegeben wurde. Wer den Freigabeweg ausführt (Web-App oder Orchestrator), ist offen, und läuft er im selben Prozess
  wie die Agentenschleife, trennt das Token nichts.
- Eine Wiederholung von `log_activity` (zum Beispiel nach Netzfehler) legt eine zweite Aktivität an, `activities` hat
  keine Eindeutigkeit. Das ist eine bekannte Grenze, die `approval_id` später behebt.
- Subdomains (`mail.firma.example` gegen `firma.example`) sind nicht zusammengeführt: Unterschiedlicher Firmenname legt
  einen zweiten Kunden an, gleicher Name ergibt den Konflikt-Fehler (Punkt 5, Fall a).
- Identity-Spalten (`GENERATED ALWAYS AS IDENTITY`) brauchen kein eigenes Sequenzrecht: Die Tests mit Datenbank schreiben
  unter der Rolle `data_service_rw`, ohne dass sie eines hätte.
- **Folgeänderungen (erledigt mit F08):** `mcp-server/CLAUDE.md` und `mcp-server/README.md`, die Platzhaltertests und
  `db/roles/data_service_rw.sql`. Die Regeltests (`test_package_sql_rules.py`, `test_role_check.py`, `test_mcp_no_leak.py`,
  `test_limits.py`) ändert nur der Mensch; sie lassen den Schreibweg und `INSERT` in `db.py` zu. Ein Test belegt, dass die
  Weiche beide Lifespans startet.
- **Offene Punkte:**
  - Freemail-Behandlung, erst wenn echte Domains zugelassen werden (heute lehnt die Datenbank alles außer `.example` ab).
  - `country` als Pflicht vom Freigabeweg und `industry` mit Standard `other` sind Annahmen (im Auftrag nicht geregelt,
    so umgesetzt).
  - Rotation der Token.
  - Entwurf der `approval_id` mit F19 bis F21.
- **Erledigt seit dem ersten Text:** Längengrenzen der Textfelder (Punkt 7), Verhalten bei Kunde mit Status `inactive`
  (`log_activity` ist erlaubt), Aufteilung von `tools/list` auf zwei Pfade gegen F06 (Auslegung in Punkt 3), Verweis in
  ADR 0004.
