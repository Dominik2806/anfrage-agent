# mcp-server

Datenservice „hoffmann-data“ (Python, MCP, Streamable HTTP). Stand F07: Zugangsschutz und drei Lesewerkzeuge
(`search_products`, `get_product`, `find_customer`), die nur lesend auf PostgreSQL zugreifen. Fünf der acht
Schnittstellen aus dem Auftrag (Kapitel 6.4) sind noch Platzhalter: `create_lead`, `log_activity`, beide Resources
und der Prompt. Der Datenservice liest über eine Nur-Lese-Rolle und prüft sie beim Start
(`docs/adr/0004-datenservice-liest-ueber-nur-lese-rolle.md`).

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
   darf (Supabase: SQL-Editor, sonst `psql`). Es ist wiederholbar: Ein zweiter Lauf ändert nichts.
2. Das Passwort getrennt setzen: `ALTER ROLE data_service_ro PASSWORD '<aus dem Passwortmanager>';`. Es steht nie im Repository.
3. `MCP_SERVER_DATABASE_URL` setzen (siehe unten): `postgresql://data_service_ro:<passwort>@<host>:<port>/<datenbank>`.
4. Nach jedem `python -m db.seed` das Skript erneut ausführen. Der Reset löscht die Tabellen und mit ihnen Rechte und Policies,
   ohne den erneuten Lauf scheitert die Startprüfung (`kein-select:<tabelle>`).

Dieser Ablauf ist noch nicht gegen Supabase erprobt.

## Start
Aus dem Ordner `mcp-server`:

```
python -m hoffmann_data
```

Der Server lauscht standardmäßig auf `127.0.0.1:8000`, der Endpunkt ist `/mcp`. Ohne gültiges Token oder ohne
`MCP_SERVER_DATABASE_URL` bricht der Start mit Exit-Code 1 ab, auf stderr steht nur der Name der Variablen.

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

## Umgebungsvariablen
Die Werte kommen aus der Umgebung, nie aus einer Datei im Projekt. Vorlage: `.env.example` (ohne Werte).

| Variable | Pflicht | Bedeutung |
|---|---|---|
| `MCP_SERVER_TOKEN` | ja | Zugangstoken. Mindestens 32 Zeichen, davon mindestens 10 verschiedene; nur druckbare ASCII-Zeichen, keine Leerzeichen, Tabulatoren oder Zeilenumbrüche. Ein ungültiges Token wird abgelehnt, nicht gekürzt. Leer ist immer ein Fehler. |
| `MCP_SERVER_DATABASE_URL` | ja | Verbindung zur Datenbank unter der Nur-Lese-Rolle `data_service_ro`: `postgresql://<rolle>:<passwort>@<host>:<port>/<datenbank>`. Dieselben Regeln wie `DATABASE_URL` des Seed-Skripts: Für jeden Host außer `localhost`, `127.0.0.1` und `::1` muss die URL `sslmode` genau einmal mit `require`, `verify-ca` oder `verify-full` enthalten. Die Parameter `host`, `hostaddr`, `service` und `dbname` sowie die Umgebungsvariablen `PGHOSTADDR` und `PGSERVICE` werden abgelehnt (Einzelheiten: `docs/DATENMODELL.md`, Abschnitt 4). Fehlermeldungen nennen nur den Namen der Variablen, nie einen Teil des Werts. |
| `MCP_SERVER_HOST` | nein | Adresse, an die der Server bindet. Standard `127.0.0.1`. Andere Werte als `127.0.0.1`, `localhost` und `::1` brauchen `MCP_SERVER_ALLOWED_HOSTS`. |
| `MCP_SERVER_PORT` | nein | Port von 1 bis 65535. Standard `8000`. |
| `MCP_SERVER_ALLOWED_HOSTS` | nur bei Host außer Loopback | Erlaubte Werte des `Host`-Headers, kommagetrennt. Siehe unten. |

Ist `MCP_SERVER_HOST`, `MCP_SERVER_PORT` oder `MCP_SERVER_ALLOWED_HOSTS` leer oder besteht nur aus
Leerzeichen, gilt sie als nicht gesetzt (Standardwert). So bleibt eine kopierte `.env.example` mit leeren Zeilen
unschädlich. `MCP_SERVER_DATABASE_URL` hat keinen Standardwert: Leer gilt auch als nicht gesetzt, dann bricht der Start ab.

Token erzeugen:

```
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Das Token gehört in eine Datei oder einen Schlüsselspeicher außerhalb des Projektordners, nie ins Repository.

## Zugangsschutz
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
  Aufruf: `POST /mcp` mit `Content-Type: application/json` und `Accept: application/json, text/event-stream`;
  die Antwort kann ein Ereignisstrom (SSE) sein.

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
Acht Schnittstellen nach Auftrag 6.4. Drei Werkzeuge lesen seit F07 echte Daten aus PostgreSQL, die übrigen fünf sind
Platzhalter.

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
  Ein exakter Treffer steht zuerst (auch wenn der Artikel ausgelaufen ist), dann aktive vor inaktiven Artikeln.
  `is_active: false` kennzeichnet ausgelaufene Artikel.
- **`find_customer`:** Immer exakt, ohne Beachtung der Schreibung. Mit `@`: zuerst der Kontakt (`matched_by` `email`), sonst die
  Domain der Adresse (`domain`). Ohne `@`: der Firmenname (`company`), sonst die Domain. Subdomains und Teilnamen treffen nicht.
  Auch ein inaktiver Kunde wird geliefert.
- **Fehler der Datenbank:** `Die Datenbank ist nicht erreichbar oder die Abfrage ist fehlgeschlagen.` Ohne Datenbank (nur in Tests,
  die die App ohne URL bauen) `Die Datenbank ist nicht konfiguriert.` Beide Meldungen sind fest und nennen nie Werte.

### Platzhalter
Sie haben eine kurze Beschreibung und keine Parameter (die Signaturen kommen mit F08 und F09). Sie liefern nie Scheindaten,
sondern den festen Fehler „Diese Schnittstelle ist noch nicht implementiert.“, ohne Eingabewerte.

| Typ | Name | Verhalten |
|---|---|---|
| Tool | `create_lead` | Ergebnis mit `isError: true` und dem festen Text |
| Tool | `log_activity` | wie oben |
| Resource | `policy://tonalitaet` | JSON-RPC-Fehler `-32603` mit dem festen Text |
| Resource | `policy://rabatte` | wie oben |
| Prompt | `antwort_entwurf` | JSON-RPC-Fehler `-32603` mit dem festen Text (`MCPError`) |

Es gibt keine Werkzeuge zum Versenden oder Löschen. Fortschrittsmeldungen bei länger laufenden Aufrufen
(Auftrag 6.4) sind nicht Teil von F07, sie kommen mit F10. Der Transport (zustandslos, Ereignisstrom) lässt
sie zu (ADR 0003).

## Hosting (F30)
Der Server spricht selbst kein TLS. Beim Hosting muss der Hoster TLS davor setzen, sonst läuft das Token
unverschlüsselt. Es gibt ein gemeinsames Token für alle Clients, ohne Rotation. Bekannte Grenzen stehen in
`docs/adr/0003-token-pruefung-eigene-middleware.md`.

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
Actions ist das ein Fehler. Die CI startet dafür PostgreSQL 17.
