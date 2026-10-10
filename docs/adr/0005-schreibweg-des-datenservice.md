# ADR 0005: Schreibweg des Datenservice

- Status: Vorgeschlagen
- Datum: 2026-10-10

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
   weitere Recht ab, auf `products`, `product_fits` und `discount_rules` jedes Recht. Es gibt keinen Schalter. ADR 0004
   bleibt unverändert gültig: Die Lese-Werkzeuge nutzen weiter nur `data_service_ro`. Verbindung, URL und Skript
   (`db/roles/data_service_rw.sql`) sind getrennt.
2. **Eigene Konfiguration.** `MCP_SERVER_WRITE_DATABASE_URL` und `MCP_SERVER_WRITE_TOKEN` sind eigene
   Umgebungsvariablen, mit denselben Regeln wie die Lese-Variablen. Beide fehlen: Der Schreibweg ist nicht verfügbar,
   `/mcp-write` antwortet nicht. Nur eine von beiden gesetzt, oder beide Token gleich: Der Start bricht ab.
3. **Zwei Token, zwei Pfade.** `/mcp` (Lese-Werkzeuge, Resources, Prompt) nur mit dem Lese-Token, `/mcp-write`
   (`create_lead`, `log_activity`) nur mit dem Schreib-Token. Zwei `MCPServer`-Instanzen laufen im selben Prozess, je
   in `BearerTokenMiddleware` mit eigenem Token. Das Schreib-Token auf `/mcp` und das Lese-Token auf `/mcp-write`
   werden je mit der einheitlichen 401-Antwort abgelehnt. Die Prüfung der erlaubten Hosts (Host-/Origin-Schutz) gilt
   für beide Apps. Eine Pfadweiche wählt allein nach dem exakten Pfad (nur `/mcp-write` geht an die Schreib-App, alles
   andere an die Lese-App), liest keinen Body und prüft kein Token.
4. **Freigabesperre als Übergang.** Das Schreib-Token hat nur der Freigabeweg, nie der Agent. Der Agent bekommt
   nur das Lese-Token und sieht die Schreib-Werkzeuge nicht. Später prüft der Server eine `approval_id` in der
   Datenbank (F19 bis F21); das ist hier nicht entschieden.
5. **Dublettenabgleich in `create_lead`.** Die Domain kommt aus der E-Mail-Adresse. Abgeglichen wird exakt und ohne
   Beachtung der Schreibung, zuerst die Domain, dann der Firmenname. Treffer: Der bestehende Kunde wird unverändert
   zurückgegeben und fehlende Kontakte werden ergänzt. Ein Konflikt (Firmenname gehört zu einem anderen Kunden als die
   Domain, oder die Adresse gehört zu einem anderen Kunden) ist ein fester Fehler, es wird nie geraten. Subdomains werden
   nicht zusammengeführt. Die UNIQUE-Constraints auf `domain`, `lower(company_name)` und `email` bleiben die zweite Sperre.
6. **Prüfungen 2, 3 und 3b** (DATENMODELL Abschnitt 5) gelten im Werkzeug: Die Domain der Kontakt-Adresse entspricht der
   Domain der Firma, und `occurred_at` und `created_at` setzt die Datenbank (`now()`, nicht die Uhr des Dienstes). Damit
   liegt nie ein Zeitstempel vor `created_at` der Firma.
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
8. **Eine Transaktion pro Aufruf.** Eigener Schreib-Verbindungsmanager in `db.py` (nicht schreibgeschützt, Zeitlimit 5 s,
   Commit am Ende, bei jedem Fehler Rollback). Feste Fehlermeldungen ohne Eingabewerte wie im Lesepfad.

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
  einen zweiten Kunden an, gleicher Name ergibt den Konflikt-Fehler.
- Annahme: Identity-Spalten (`GENERATED ALWAYS AS IDENTITY`) brauchen kein eigenes Sequenzrecht. Ein Test mit Datenbank
  belegt das.
- **Folgeänderungen:** `mcp-server/CLAUDE.md` (Zeilen 15-16, 24, 33, 41-42), die Platzhaltertests
  (`mcp_testkit.py:64-67`, `test_mcp_interfaces.py`, `test_mcp_limits.py:23`) und `db/roles/data_service_rw.sql`.
  Die Regeltests (`test_package_sql_rules.py`, `test_role_check.py`, `test_mcp_no_leak.py`, `test_limits.py`) ändert nur der
  Mensch; sie müssen den Schreibweg und `INSERT` in `db.py` zulassen. Ein Test belegt, dass die Weiche beide Lifespans
  startet (Annahme aus dem Quelltext des SDK).
- **Offene Punkte:**
  - Freemail-Behandlung, erst wenn echte Domains zugelassen werden (heute lehnt die Datenbank alles außer `.example` ab).
  - `country` als Pflicht vom Freigabeweg und `industry` mit Standard `other` sind Annahmen (im Auftrag nicht geregelt).
  - Längengrenzen der Textfelder in `limits.py`.
  - Verhalten bei Kunde mit Status `inactive` (Annahme: `log_activity` erlaubt).
  - Aufteilung von `tools/list` auf zwei Pfade gegen F06 („listet seine Schnittstellen auf“).
  - Rotation der Token.
  - Entwurf der `approval_id` mit F19 bis F21.
  - Ob in ADR 0004 ein Verweis auf diesen ADR ergänzt wird.
