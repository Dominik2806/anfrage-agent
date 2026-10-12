# mcp-server

Datenservice „hoffmann-data“ (Python, MCP, Streamable HTTP). Stand F08: Zugangsschutz, drei Lesewerkzeuge
(`search_products`, `get_product`, `find_customer`) und zwei Schreibwerkzeuge (`create_lead`, `log_activity`). Fünf der acht
Schnittstellen aus dem Auftrag (Kapitel 6.4) sind damit echt; Platzhalter sind nur noch beide Resources und der Prompt.
Zwei Wege, zwei Rollen, zwei Token: Auf `/mcp` liest der Datenservice über eine Nur-Lese-Rolle
(`docs/adr/0004-datenservice-liest-ueber-nur-lese-rolle.md`), auf `/mcp-write` legt er über eine eigene Schreibrolle
nur an und überschreibt oder löscht nichts (`docs/adr/0005-schreibweg-des-datenservice.md`). Beide Rollen prüft er beim Start.

## Einrichtung
Aus dem Hauptordner des Projekts:

```
python -m pip install -r mcp-server/requirements.txt
```

Unter Windows mit der virtuellen Umgebung: `.\.venv\Scripts\python.exe -m pip install -r mcp-server/requirements.txt`.

## Datenbankrolle einspielen
Der Datenservice liest nur, als Rolle `data_service_ro` (ADR 0004). Das Skript `db/roles/data_service_ro.sql` legt sie an:
Anmelden erlaubt, kein Superuser, kein `BYPASSRLS`, `SELECT` auf `products`, `product_fits`, `customers`, `contacts` und
`activities` mit je einer `SELECT`-Policy, `discount_rules` nicht lesbar (kommt mit F09). Das Skript enthält kein Passwort.

1. Das Skript im Schema der Tabellen ausführen (Standard `public`), mit einem Benutzer, der Rollen anlegen und Rechte vergeben
   darf (Supabase: SQL-Editor, sonst `psql`; der SQL-Editor läuft nicht als Superuser, siehe unten). Es ist wiederholbar: Ein zweiter
   Lauf ändert nichts.
2. Das Passwort getrennt setzen: `ALTER ROLE data_service_ro PASSWORD '<aus dem Passwortmanager>';`. Es steht nie im Repository.
3. `MCP_SERVER_DATABASE_URL` setzen (siehe unten): `postgresql://data_service_ro:<passwort>@<host>:<port>/<datenbank>`.
4. Nach jedem `python -m db.seed` das Skript erneut ausführen. Der Reset löscht die Tabellen und mit ihnen Rechte und Policies,
   ohne den erneuten Lauf scheitert die Startprüfung (`kein-select:<tabelle>`).

### Hinweise für Supabase
- Der SQL-Editor läuft als Benutzer `postgres`, der kein Superuser ist. Das Skript nennt deshalb die Option `SUPERUSER` nicht.
- Die Verbindung geht über den Session Pooler (Port 5432). Der Benutzer heißt `data_service_ro.<Projektkennung>`:
  `postgresql://data_service_ro.<projektkennung>:<passwort>@<pooler-host>:5432/<datenbank>?sslmode=require`
- Das Passwort nur aus Buchstaben und Ziffern wählen, sonst muss es in der URL prozentkodiert werden.
- Das Snippet mit `ALTER ROLE ... PASSWORD` enthält das Passwort. Nach dem Ausführen im SQL-Editor löschen.

Erprobt am 2026-10-09 gegen Supabase über den Session Pooler.

## Schreibrolle einspielen (optional)
Der Schreibweg (`/mcp-write`, ADR 0005) ist nur verfügbar, wenn er konfiguriert ist. Er verbindet sich als Rolle
`data_service_rw`; das Skript `db/roles/data_service_rw.sql` legt sie an: Anmelden erlaubt, kein Superuser, kein `BYPASSRLS`,
`SELECT` und `INSERT` auf `customers`, `contacts` und `activities`, sonst nichts (kein `UPDATE`, kein `DELETE`, kein `TRUNCATE`,
kein Recht auf `products`, `product_fits` und `discount_rules`), je eine `SELECT`-Policy (`rw_select`) und eine `INSERT`-Policy
(`rw_insert`) je Tabelle. Die `INSERT`-Policies sind eng: Auf `customers` nur `status = 'lead'`, auf `activities` nur
`created_by = 'agent'`, nur die Typen `inquiry`, `complaint` und `note` und ohne Artikelbezug, auf `contacts` ohne Einschränkung.
Jede Abfrage der Rolle hat ein Zeitlimit von 5 Sekunden. Das Skript enthält kein Passwort.

1. Das Skript im Schema der Tabellen ausführen, wie bei der Nur-Lese-Rolle (wiederholbar).
2. Das Passwort getrennt setzen: `ALTER ROLE data_service_rw PASSWORD '<aus dem Passwortmanager>';`. Es steht nie im Repository.
3. `MCP_SERVER_WRITE_DATABASE_URL` und `MCP_SERVER_WRITE_TOKEN` setzen (siehe unten).
4. Nach jedem `python -m db.seed` auch dieses Skript erneut ausführen, sonst scheitert die Startprüfung des Schreibwegs.

Bei Supabase gelten dieselben Hinweise wie oben; der Benutzer heißt `data_service_rw.<Projektkennung>`.

## Start
Aus dem Ordner `mcp-server`:

```
python -m hoffmann_data
```

Der Server lauscht standardmäßig auf `127.0.0.1:8000`. Er hat zwei Endpunkte: `/mcp` (Lesewerkzeuge, Resources und Prompt, nur
mit dem Lese-Token) und `/mcp-write` (`create_lead` und `log_activity`, nur mit dem Schreib-Token). `/mcp-write` gibt es nur,
wenn der Schreibweg konfiguriert ist; sonst ist `/mcp-write` ein unbekannter Pfad der Lese-App (ohne Lese-Token 401). Ohne
gültiges Token oder ohne `MCP_SERVER_DATABASE_URL` bricht der Start mit Exit-Code 1 ab, auf stderr steht nur der Name der
Variablen. Dasselbe gilt, wenn nur eine der beiden Schreibvariablen gesetzt ist oder beide Token gleich sind.

### Startprüfung der Datenbankrolle
Vor dem Start prüft der Datenservice, dass die Rolle der Verbindung wirklich nur lesen darf. Die Prüfung läuft immer, es gibt
keinen Schalter dafür. Scheitert sie, endet der Start mit Exit-Code 1, uvicorn startet nicht, und auf stderr steht
`Prüfung der Datenbankrolle fehlgeschlagen:` und die Kurzbezeichnungen der gescheiterten Prüfungen. Nie stehen URL,
Rollenname, Host, Benutzer, Datenbankname, Passwort oder Token in der Meldung.

| Kurzbezeichnung | Bedeutung | Abhilfe |
|---|---|---|
| `rolle-ist-superuser` | Die Rolle ist ein Superuser. | Die URL der Rolle `data_service_ro` verwenden. |
| `rolle-umgeht-rls` | Die Rolle hat `BYPASSRLS`. | Eine Rolle ohne `BYPASSRLS` verwenden. |
| `schreibrecht:<RECHT>:<tabelle>` | Die Rolle darf `INSERT`, `UPDATE`, `DELETE` oder `TRUNCATE` auf der Tabelle (`INSERT` und `UPDATE` auch als Spaltenrecht). | Das Skript erneut ausführen (es entzieht alle Tabellenrechte und vergibt nur `SELECT`), Mitgliedschaften der Rolle prüfen. |
| `kein-select:<tabelle>` | Die Rolle darf die Tabelle nicht lesen. | Das Skript erneut ausführen (üblich nach einem Seed-Reset). |
| `create-im-schema` | Die Rolle darf im aktuellen Schema Objekte anlegen. Unter PostgreSQL bis Version 14 hat PUBLIC dieses Recht auf `public`. | Das Recht entziehen: `REVOKE CREATE ON SCHEMA <schema> FROM <Rolle>;`, kommt es über `PUBLIC`, zusätzlich `REVOKE CREATE ON SCHEMA <schema> FROM PUBLIC;` (betrifft alle Rollen, der Mensch prüft das vorher). |
| `tabelle-fehlt:<tabelle>` | Die Tabelle ist über den `search_path` nicht auffindbar. | Datenbank und Schema der URL prüfen. Das Seed-Skript löscht vorhandene Tabellen samt Daten und führt nur der Mensch aus (Einzelheiten: `docs/DATENMODELL.md`, Abschnitt 4). |
| `datenbankfehler` | Verbindung oder Abfrage sind gescheitert (nicht erreichbar, falsche Zugangsdaten, Zeitlimit von 5 Sekunden). | URL, Netz und Zugangsdaten prüfen. Das Log hat genau ein WARNING mit Klasse und SQLSTATE. |

Die Prüfung ist eine Momentaufnahme beim Start und deckt nur diese fünf Tabellen und das Recht `CREATE` im aktuellen Schema ab.
Die Datenbank bleibt die eigentliche Sperre (ADR 0004, Abschnitt „Grenzen der Startprüfung“).

### Startprüfung der Schreibrolle
Ist der Schreibweg konfiguriert, prüft der Datenservice nach der Leseprüfung auch die Rolle von
`MCP_SERVER_WRITE_DATABASE_URL`: Sie darf auf `customers`, `contacts` und `activities` genau lesen und anlegen, auf `products`,
`product_fits` und `discount_rules` nichts. Es gibt keinen Schalter. Scheitert die Prüfung, endet der Start mit Exit-Code 1, und
auf stderr steht `Schreibweg:` und die Kurzbezeichnungen der gescheiterten Prüfungen, nie URL, Rollenname, Host, Benutzer,
Datenbankname, Passwort oder Token.

| Kurzbezeichnung | Bedeutung | Abhilfe |
|---|---|---|
| `rolle-ist-superuser` | Die Rolle ist ein Superuser. | Die URL der Rolle `data_service_rw` verwenden. |
| `rolle-umgeht-rls` | Die Rolle hat `BYPASSRLS`. | Eine Rolle ohne `BYPASSRLS` verwenden. |
| `schreibrecht:<RECHT>:<tabelle>` | Die Rolle darf `UPDATE`, `DELETE` oder `TRUNCATE` auf `customers`, `contacts` oder `activities` (`UPDATE` auch als Spaltenrecht), oder auf `products`, `product_fits` oder `discount_rules` auch `INSERT`. | Das Skript `db/roles/data_service_rw.sql` erneut ausführen (es entzieht alle Tabellenrechte und vergibt nur `SELECT` und `INSERT`), Mitgliedschaften der Rolle prüfen. |
| `lesen-unerlaubt:<tabelle>` | Die Rolle darf `products`, `product_fits` oder `discount_rules` lesen. | Das Skript erneut ausführen. |
| `kein-select:<tabelle>` | Die Rolle darf `customers`, `contacts` oder `activities` nicht lesen. | Das Skript erneut ausführen (üblich nach einem Seed-Reset). |
| `fehlendes-recht:INSERT:<tabelle>` | Die Rolle darf auf `customers`, `contacts` oder `activities` nicht anlegen. | Das Skript erneut ausführen. |
| `create-im-schema` | Die Rolle darf im aktuellen Schema Objekte anlegen. | Wie bei der Leseprüfung (Tabelle oben). |
| `tabelle-fehlt:<tabelle>` | Eine der sechs Tabellen ist über den `search_path` nicht auffindbar. | Datenbank und Schema der URL prüfen. |
| `datenbankfehler` | Verbindung oder Abfrage sind gescheitert. | URL, Netz und Zugangsdaten prüfen. Das Log hat genau ein WARNING mit Klasse und SQLSTATE. |

Die Prüfung betrachtet Rechte, nicht die Bedingungen der Policies; die engen `INSERT`-Bedingungen (Status `lead`, nur Agent, nur
drei Typen, kein Artikelbezug) setzt die Datenbank durch.

## Umgebungsvariablen
Die Werte kommen aus der Umgebung, nie aus einer Datei im Projekt. Vorlage: `.env.example` (ohne Werte).

| Variable | Pflicht | Bedeutung |
|---|---|---|
| `MCP_SERVER_TOKEN` | ja | Zugangstoken. Mindestens 32 Zeichen, davon mindestens 10 verschiedene; nur druckbare ASCII-Zeichen, keine Leerzeichen, Tabulatoren oder Zeilenumbrüche. Ein ungültiges Token wird abgelehnt, nicht gekürzt. Leer ist immer ein Fehler. |
| `MCP_SERVER_DATABASE_URL` | ja | Verbindung zur Datenbank unter der Nur-Lese-Rolle `data_service_ro`: `postgresql://<rolle>:<passwort>@<host>:<port>/<datenbank>`. Dieselben Regeln wie `DATABASE_URL` des Seed-Skripts: Für jeden Host außer `localhost`, `127.0.0.1` und `::1` muss die URL `sslmode` genau einmal mit `require`, `verify-ca` oder `verify-full` enthalten. Die Parameter `host`, `hostaddr`, `service` und `dbname` sowie die Umgebungsvariablen `PGHOSTADDR` und `PGSERVICE` werden abgelehnt (Einzelheiten: `docs/DATENMODELL.md`, Abschnitt 4). Fehlermeldungen nennen nur den Namen der Variablen, nie einen Teil des Werts. |
| `MCP_SERVER_WRITE_DATABASE_URL` | nur zusammen mit `MCP_SERVER_WRITE_TOKEN` | Verbindung zur Datenbank unter der Schreibrolle `data_service_rw`, für den Schreibweg. Dieselben Regeln wie `MCP_SERVER_DATABASE_URL`. Beide Schreibvariablen setzen oder beide weglassen; nur eine gesetzt ist ein Startfehler. |
| `MCP_SERVER_WRITE_TOKEN` | nur zusammen mit `MCP_SERVER_WRITE_DATABASE_URL` | Zugangstoken für `/mcp-write`. Dieselben Regeln wie `MCP_SERVER_TOKEN`, aber **nicht gleich** dem Lese-Token (sonst Startfehler). Das Token hat nur der Freigabeweg, nie der Agent. |
| `MCP_SERVER_HOST` | nein | Adresse, an die der Server bindet. Standard `127.0.0.1`. Andere Werte als `127.0.0.1`, `localhost` und `::1` brauchen `MCP_SERVER_ALLOWED_HOSTS`. |
| `MCP_SERVER_PORT` | nein | Port von 1 bis 65535. Standard `8000`. |
| `MCP_SERVER_ALLOWED_HOSTS` | nur bei Host außer Loopback | Erlaubte Werte des `Host`-Headers, kommagetrennt. Siehe unten. |

Ist `MCP_SERVER_HOST`, `MCP_SERVER_PORT` oder `MCP_SERVER_ALLOWED_HOSTS` leer oder besteht nur aus
Leerzeichen, gilt sie als nicht gesetzt (Standardwert). So bleibt eine kopierte `.env.example` mit leeren Zeilen
unschädlich. `MCP_SERVER_DATABASE_URL` hat keinen Standardwert: Leer gilt auch als nicht gesetzt, dann bricht der Start ab.
Die beiden Schreibvariablen sind leer ebenfalls „nicht gesetzt“: Sind beide leer, gibt es keinen Schreibweg.

Token erzeugen:

```
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Das Token gehört in eine Datei oder einen Schlüsselspeicher außerhalb des Projektordners, nie ins Repository.

## Zugangsschutz
- Zwei Token, zwei Pfade (ADR 0005): Das Lese-Token gilt nur auf `/mcp`, das Schreib-Token nur auf `/mcp-write`. Das jeweils
  andere Token wird auf dem Pfad mit derselben einheitlichen 401-Antwort abgelehnt wie ein falsches. Eine Pfadweiche
  (`hoffmann_data/switch.py`) wählt allein nach dem exakten Pfad: Nur `/mcp-write` geht an die Schreib-App, alles andere an die
  Lese-App. Sie liest keinen Body und prüft kein Token; die Prüfung macht die Middleware der gewählten App. Host-/Origin-Schutz,
  die Größengrenze und der zustandslose Betrieb gelten für beide Pfade. Das Schreib-Token hat nur der Freigabeweg, der Agent
  bekommt nur das Lese-Token und sieht die Schreibwerkzeuge nicht.
- Jede Anfrage braucht genau den Header `Authorization: Bearer <token>`. Leerzeichen am Anfang oder Ende des
  Headerwerts entfernt schon der Webserver (HTTP-Regel). Abgelehnt werden Abweichungen im Inneren (zwei
  Leerzeichen, Tabulator) und andere Schreibweisen des Schemas (`bearer`, `BEARER`). Die Tests prüfen das
  auf App-Ebene. Das Token in der URL gilt nicht.
- Jede Route ohne gültiges Token antwortet mit 401 und immer demselben Text, auch Pfade, die es nicht gibt.
  Der Vergleich läuft in konstanter Zeit (`hmac.compare_digest`).
- WebSocket-Verbindungen werden abgelehnt.
- Der Host-/Origin-Schutz des SDKs ist immer an. Falscher Host: 421, falscher Origin: 403.
- Kein Zugriffsprotokoll: uvicorn läuft mit `access_log=False`, damit Pfad und Query (und ein falsch
  platziertes Token) nicht im Protokoll landen. Das SDK protokolliert abgelehnte Host-/Origin-Werte,
  das ist Fremdtext und enthält nie das Token.
- Eine Anfrage darf höchstens 256 KiB groß sein, größere Anfragen mit gültigem Token bekommen 413. Ohne
  gültiges Token bleibt es bei 401. Die Eingabelängen je Werkzeug (Suchtext, Artikelnummer, Kundenangabe) stehen
  unter „Schnittstellen“.
- Der Server arbeitet zustandslos (keine Sitzungen). Eine Anfrage braucht kein `initialize`.
  Aufruf: `POST /mcp` (oder `POST /mcp-write`) mit `Content-Type: application/json` und
  `Accept: application/json, text/event-stream`; die Antwort kann ein Ereignisstrom (SSE) sein.

### `MCP_SERVER_ALLOWED_HOSTS`
Die Liste nennt die genauen Werte, die im `Host`-Header ankommen dürfen, z. B. `daten.example.org`.
- Mit `:*` am Ende gilt jeder Port: `daten.example.org:*`.
- Der Port muss mit angegeben werden, wenn er nicht 80 (http) oder 443 (https) ist, z. B. `daten.example.org:8000`.
- Einträge sind durch Kommas getrennt. Leere Einträge und Leerzeichen innerhalb eines Eintrags sind ungültig.
- Auf `127.0.0.1`, `localhost` und `::1` gelten die Loopback-Werte immer, die Liste ergänzt sie.
- Bei einem Host außer Loopback gilt nur die Liste. Anfragen mit einem `Origin`-Header (Browser) werden dort
  mit 403 abgelehnt, der Agent sendet keinen.

Ohne die Liste startet der Server nur auf `127.0.0.1`.

## Schnittstellen
Acht Schnittstellen nach Auftrag 6.4. Fünf Werkzeuge sind echt: drei lesen seit F07 Daten aus PostgreSQL (Pfad `/mcp`), zwei
legen seit F08 Daten an (Pfad `/mcp-write`). Drei Schnittstellen sind Platzhalter (beide Resources und der Prompt, Pfad `/mcp`).
Auslegung von F06 („listet seine Schnittstellen auf“): Jeder Endpunkt listet seine eigenen Schnittstellen, beide zusammen sind
die acht (ADR 0005).

### Lesewerkzeuge
| Name | Eingabe | Ergebnis (`structuredContent`) |
|---|---|---|
| `search_products` | `query`: Suchtext, 1 bis 200 Zeichen. `limit`: ganze Zahl von 1 bis 20, Standard 5. | `{"items": [{article_number, name, category, is_active}], "count": n}` |
| `get_product` | `article_number`: Format `AB-1234` oder `AB-1234-X1`, Schreibweise egal. | `{"product": {...}}` mit Preis, Einheit, Lieferzeit, technischen Daten und den Zuordnungen `fits_assemblies` und `compatible_parts`; bei unbekannter Nummer `{"product": null}` |
| `find_customer` | `query`: Firmenname, E-Mail-Adresse oder Domain, 1 bis 200 Zeichen. | `{"customer": {...}, "matched_by": ...}` mit Stammdaten, bis zu 20 Kontakten und den letzten 10 Aktivitäten; bei unbekanntem Kunden beide Felder `null` |

- **Nichttreffer** sind kein Fehler: `search_products` liefert eine leere Liste, die beiden anderen `null`.
- **Eingaben** werden vor der Datenbank geprüft. Suchtext und Kundenangabe: nach `strip()` 1 bis 200 Zeichen, ohne Steuer-,
  Format-, Zeilen- und Absatztrennzeichen (Unicode-Kategorien C* sowie Zl und Zp). Artikelnummer: reines ASCII. `limit`: nur eine
  echte ganze Zahl. Eine ungültige Eingabe ergibt einen festen Fehler (`isError: true`) ohne den Eingabewert, die Datenbank wird
  nicht berührt. Texte, die als JSON `null`, Liste oder Objekt lesbar sind (`null`, `[]`, `{}`), gelten als ungültig.
- **Ausgaben:** Preise und Beträge als Text mit zwei Nachkommastellen, Zeiten als ISO-Text in UTC, keine internen IDs.
- **Suche:** Volltext (deutsch) über Name und Beschreibung: Der Suchtext geht unverändert an `websearch_to_tsquery` (`catalog.py`, `SEARCH_SQL`).
  Mehrere Wörter ohne Operator verknüpft PostgreSQL mit UND; die Syntax dieser Funktion (`or`, Anführungszeichen, `-`) wird nicht ausgeschlossen,
  ihr Verhalten ist hier nicht zugesichert und nicht getestet. Dazu kommt die Artikelnummer exakt oder als Präfix.
  Es gibt keinen Volltextindex; ein GIN-Index wäre ein eigener späterer Schritt mit eigenem Pull Request.
  Ein exakter Treffer steht zuerst (auch wenn der Artikel ausgelaufen ist), dann aktive vor inaktiven Artikeln.
  `is_active: false` kennzeichnet ausgelaufene Artikel.
- **`find_customer`:** Immer exakt, ohne Beachtung der Schreibung. Mit `@`: zuerst der Kontakt (`matched_by` `email`), sonst die
  Domain der Adresse (`domain`). Ohne `@`: der Firmenname (`company`), sonst die Domain. Subdomains und Teilnamen treffen nicht.
  Auch ein inaktiver Kunde wird geliefert.
- **Fehler der Datenbank:** `Die Datenbank ist nicht erreichbar oder die Abfrage ist fehlgeschlagen.` Ohne Datenbank (nur in Tests,
  die die App ohne URL bauen) `Die Datenbank ist nicht konfiguriert.` Beide Meldungen sind fest und nennen nie Werte.

### Schreibwerkzeuge (Pfad `/mcp-write`, Schreib-Token)
| Name | Eingabe | Ergebnis (`structuredContent`) |
|---|---|---|
| `create_lead` | Pflicht: `company_name` (1 bis 200 Zeichen), `contact_first_name` und `contact_last_name` (je 1 bis 100), `contact_email`, `language` (`de` oder `en`), `country` (`de`, `at` oder `ch`). Optional: `contact_job_title` (höchstens 100, leer gilt als nicht angegeben), `industry` (`automotive`, `food`, `packaging`, `metalworking`, `plastics`, `logistics` oder `other`, Standard `other`). | `{"customer_domain": ..., "customer_created": bool, "contact_created": bool}` |
| `log_activity` | Pflicht: `customer_domain`, `type` (`inquiry`, `complaint` oder `note`), `subject` (1 bis 200 Zeichen), `summary` (1 bis 2000). Optional: `contact_email` (Adresse eines Kontakts dieses Kunden). | `{"customer_domain": ..., "type": ..., "logged": true}` |

- **Nur anlegen:** Beide Werkzeuge legen neue Zeilen an und überschreiben oder löschen nichts. Der Status eines Leads ist immer
  `lead` und nicht vom Aufrufer änderbar, eine Aktivität entsteht immer als `agent`, ohne Betrag und ohne Artikelbezug.
  `created_at` und `occurred_at` setzt die Datenbank (`now()`), nicht der Aufrufer. Es gibt keine interne ID in der Ausgabe.
- **Eingaben** werden vor der Datenbank in `limits.py` geprüft, mit einer festen Meldung je Feld, die den Wert nie nennt.
  Firmenname, Namen und Position sind einzeilig und ohne Steuer-, Format- und Trennzeichen; Betreff und Zusammenfassung erlauben
  Zeilenumbruch, Wagenrücklauf und Tab. Aufzählungen werden exakt verglichen (ohne `strip`, ohne Kleinschreiben). Die
  E-Mail-Adresse hat höchstens 254 Zeichen, genau ein `@` und eine Domain auf `.example`; sie wird ganz kleingeschrieben. Die
  Domain (`customer_domain`) hat höchstens 253 Zeichen, nur ASCII und `.example`. Ein leerer Text bei `contact_email` ist
  ungültig, nur ein fehlendes Feld heißt „kein Kontakt“. Die Reihenfolge der Prüfung ist die Reihenfolge der Parameter.
- **`create_lead`, Dublettenschutz** (ADR 0005, Punkt 5): Die Domain des Kunden kommt aus der E-Mail-Adresse. Gesucht wird
  zuerst über die Domain, dann über den Firmennamen, ohne Beachtung der Schreibung, und über die Adresse. Ein Treffer über die
  Domain liefert den bestehenden Kunden **unverändert** zurück (`customer_created: false`); ein Kontakt, den es noch nicht gibt,
  wird ergänzt (`contact_created: true`), ein vorhandener bleibt unverändert. Ein Treffer nur über den Firmennamen, Domain und
  Name auf verschiedenen Kunden oder eine Adresse, die einem anderen Kunden gehört, ist ein Konflikt: feste Meldung
  (`Der Lead steht in Konflikt mit bestehenden Daten …`), es wird nichts geschrieben, ein Mitarbeiter entscheidet.
  Unterdomains (`mail.firma.example`) werden nicht mit `firma.example` zusammengeführt. Die drei Nachschlagungen laufen in einer
  SQL-Anweisung (eine Momentaufnahme).
- **Rennen:** Legen zwei Aufrufe gleichzeitig denselben neuen Kunden an, scheitert einer an der Eindeutigkeit der Datenbank. Er
  wiederholt den ganzen Ablauf genau einmal in einer neuen Transaktion und liefert dann den bestehenden Kunden. Scheitert auch das,
  kommt die feste Meldung `Der Eintrag existiert bereits.`; andere Fehler werden nicht wiederholt.
- **`log_activity`:** Der Kunde wird über die Domain gefunden; ein unbekannter Kunde ist ein fester Fehler, es wird nie ein Kunde
  angelegt. Ein Kunde mit Status `inactive` ist erlaubt. Eine Adresse wird nur bei diesem Kunden gesucht: „unbekannt“ und „gehört
  zu einem anderen Kunden“ haben dieselbe Meldung. Ein zweiter gleicher Aufruf legt einen zweiten Eintrag an (eine bekannte Grenze,
  ADR 0005).
- **Fehler:** Alle Meldungen sind fest und nennen nie Eingabewerte. Neben den Meldungen je Feld gibt es die für unbekannten Kunden,
  unbekannten Kontakt, Konflikt, doppelten Eintrag, Datenbankfehler und fehlende Datenbank (`Die Datenbank ist nicht
  konfiguriert.`). Ein Fehler der Datenbank rollt die Transaktion zurück, es bleibt nichts halb geschrieben.

### Platzhalter (Pfad `/mcp`)
Beide Resources und der Prompt sind noch nicht umgesetzt (die Signaturen kommen mit F09). Sie haben eine kurze Beschreibung und
keine Parameter. Sie liefern nie Scheindaten, sondern den festen Fehler „Diese Schnittstelle ist noch nicht implementiert.“, ohne
Eingabewerte.

| Typ | Name | Verhalten |
|---|---|---|
| Resource | `policy://tonalitaet` | JSON-RPC-Fehler `-32603` mit dem festen Text |
| Resource | `policy://rabatte` | wie oben |
| Prompt | `antwort_entwurf` | JSON-RPC-Fehler `-32603` mit dem festen Text (`MCPError`) |

Es gibt keine Werkzeuge zum Versenden oder Löschen. Fortschrittsmeldungen bei länger laufenden Aufrufen
(Auftrag 6.4) sind nicht Teil von F07, sie kommen mit F10. Der Transport (zustandslos, Ereignisstrom) lässt
sie zu (ADR 0003).

## Hosting (F30)
Der Server spricht selbst kein TLS. Beim Hosting muss der Hoster TLS davor setzen, sonst läuft das Token
unverschlüsselt. Es gibt ein Lese-Token für alle lesenden Clients und ein Schreib-Token für den Freigabeweg, beide ohne
Rotation. Bekannte Grenzen stehen in `docs/adr/0003-token-pruefung-eigene-middleware.md` und
`docs/adr/0005-schreibweg-des-datenservice.md`.

## Hinweis zu PowerShell 5.1
Der Server antwortet in UTF-8. Die 401-Antwort nennt das ausdrücklich (`charset=utf-8`), die Antwort als
Ereignisstrom (`text/event-stream`) nennt keinen Zeichensatz. Windows PowerShell 5.1 zeigt UTF-8-Antworten ohne
Zeichensatz als Latin-1 an, Umlaute wirken dann falsch (z. B. „ü“ als „Ã¼“). Der Server ist korrekt, es ist eine Eigenheit
der Anzeige. Die Antwort mit einem Werkzeug lesen, das UTF-8 voraussetzt, oder in PowerShell 7 prüfen.

## Tests
Aus dem Hauptordner:

```
python -m pytest tests/mcp_server
```

Die App läuft im Speicher, es wird kein Port geöffnet. Tests zu Token, Konfiguration und Schnittstellen brauchen keine
Datenbank. Die übrigen brauchen `TEST_DATABASE_URL` (nur `localhost`, Port 5432, nie die echte Datenbank): Sie legen ein
eigenes Schema an und rollen jeden Test zurück. Die Rollen-Tests brauchen das Recht `CREATEROLE`, die Tests für Superuser und
`BYPASSRLS` einen Superuser als Testbenutzer. Ohne die Variable werden die Tests mit Datenbank lokal übersprungen, in GitHub
Actions ist das ein Fehler. Ist die Variable gesetzt, die Datenbank aber nicht erreichbar (Zeitlimit 5 Sekunden), scheitern die
Tests mit Datenbank mit fester Meldung, sie werden nicht übersprungen. Die CI startet dafür PostgreSQL 17.

Ausnahme vom Zurückrollen sind die Tests der Schreibwerkzeuge (`test_create_lead.py`, `test_log_activity.py`): Sie committen echte
Zeilen ins Testschema. Alle tragen den Marker `mcp-schreibtest` in der Domain; aufgeräumt wird vor und nach jedem Test und nur, was
den Marker trägt. Sie legen die Rolle `rw_test_fixture` aus dem Skript `data_service_rw.sql` an (Anmeldung mit Zufallspasswort auf
localhost, dafür braucht der Testbenutzer `CREATEROLE`) und entfernen sie am Ende des Moduls wieder. Im Testschema ist Autovacuum
abgeschaltet, weil ein Server-Arbeiter sonst mit dem `GRANT` und `DROP POLICY` des Nur-Lese-Skripts in einen Sperrzyklus geraten
kann (siehe `AGENT_LOG.md`).
