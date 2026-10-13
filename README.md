# Anfrage-Agent

> **Hinweis: fiktives Projekt.** Dies ist ein Portfolio-Projekt. Die Hoffmann Maschinenbau GmbH,
> der Auftrag, alle Kunden, Kontakte, Artikel, Preise und Kundenanfragen sind frei erfunden.
> Übereinstimmungen mit realen Firmen oder Personen sind zufällig. Alle E-Mail-Adressen und Domains
> enden auf `.example` und sind nicht erreichbar. Es werden keine echten Kundendaten verarbeitet.
>
> **Note: fictional project.** This is a portfolio project. Hoffmann Maschinenbau GmbH, the assignment,
> and all customers, contacts, articles, prices and customer requests are made up. Any resemblance to
> real companies or people is coincidental. All email addresses and domains end in `.example`.

KI-Agent zur Vorbearbeitung von Kundenanfragen für die Hoffmann Maschinenbau GmbH.
Er klassifiziert Anfragen, recherchiert in Katalog und CRM und erstellt
Antwortentwürfe, die ein Mensch freigibt.

**Status:** in Entwicklung, Meilenstein M0

## Datenbank befüllen
Voraussetzung: Python 3.13 und `pip install -r requirements-dev.txt`.

1. Umgebungsvariable `DATABASE_URL` auf die Datenbank setzen. Der Wert gehört nie ins Repository, die Namen stehen in `.env.example`.
   Für jeden Host außer `localhost` muss die URL den Parameter `sslmode=require` enthalten (oder `verify-ca`, `verify-full`). `require` verschlüsselt, prüft aber das Serverzertifikat nicht.
2. `python -m db.seed` ausführen (unter Windows `.\.venv\Scripts\python.exe -m db.seed`). Das legt die Tabellen an und befüllt sie.
3. Sind die Tabellen schon vorhanden, setzt man zusätzlich `SEED_CONFIRM_RESET` auf genau den Host der Datenbank.
   Achtung: Der Reset löscht auch Zeilen, die Agent und Mitarbeitende später angelegt haben.

`python -m db.seed --nur-rabatte` erzeugt nur `data/richtlinien/rabatte.md` und braucht keine Datenbank.
Einzelheiten: [Datenmodell, Abschnitt 4](docs/DATENMODELL.md).

## Datenservice
Der Datenservice „hoffmann-data“ (`mcp-server/`) liefert dem Agenten Katalog und CRM zum Lesen (Endpunkt `/mcp`). Zwei Schreibwerkzeuge legen Leads und Aktivitäten an (Endpunkt `/mcp-write`, eigenes Token, eigene Rolle): Sie legen nur an und überschreiben oder löschen nichts. Voraussetzung ist eine befüllte Datenbank.

1. Das Skript `db/roles/data_service_ro.sql` in der Datenbank ausführen. Es legt die Nur-Lese-Rolle `data_service_ro` an, ohne Passwort. Nach jedem `python -m db.seed` muss es erneut ausgeführt werden.
2. Das Passwort der Rolle getrennt setzen: `ALTER ROLE data_service_ro PASSWORD '<aus dem Passwortmanager>';`
3. Die Umgebungsvariablen `MCP_SERVER_TOKEN` und `MCP_SERVER_DATABASE_URL` setzen. Die Werte gehören nie ins Repository, die Namen stehen in `.env.example`.
4. `pip install -r mcp-server/requirements.txt`, dann aus dem Ordner `mcp-server` `python -m hoffmann_data` (unter Windows `..\.venv\Scripts\python.exe -m hoffmann_data`).

Beim Start prüft der Datenservice die Rolle. Darf sie mehr als lesen, endet der Start mit Exit-Code 1, und auf stderr stehen nur die Kurzbezeichnungen der gescheiterten Prüfungen (zum Beispiel `schreibrecht:INSERT:products`), nie URL oder Zugangsdaten. Einzelheiten: [mcp-server/README.md](mcp-server/README.md).

**Schreibweg (optional):**
1. Das Skript `db/roles/data_service_rw.sql` in der Datenbank ausführen. Es legt die Rolle `data_service_rw` an (`SELECT` und `INSERT` auf `customers` und `contacts`, auf `activities` nur `INSERT`), ohne Passwort. Auch dieses Skript nach jedem `python -m db.seed` erneut ausführen; das gilt ebenso für eine Rolle mit älterem Stand (das Skript entzieht das alte `SELECT` auf `activities`, bis dahin bricht der Start mit `Schreibweg: lesen-unerlaubt:activities` ab).
2. Das Passwort getrennt setzen: `ALTER ROLE data_service_rw PASSWORD '<aus dem Passwortmanager>';`
3. `MCP_SERVER_WRITE_DATABASE_URL` und `MCP_SERVER_WRITE_TOKEN` setzen (beide oder keine; das Schreib-Token muss sich vom Lese-Token unterscheiden und gehört nur dem Freigabeweg, nicht dem Agenten). Die Namen stehen in `.env.example`.
4. Start wie oben. Der Datenservice prüft dann auch die Schreibrolle (Meldungen auf stderr mit dem Präfix `Schreibweg:`) und bedient `/mcp-write` zusätzlich zu `/mcp`.

## Dokumente
- [Projektauftrag](docs/AUFTRAG.md)
- [Datenservice](mcp-server/README.md)
- [Architekturentscheidungen](docs/adr/)
- [Entwicklungsprotokoll](AGENT_LOG.md)
- [Änderungen](CHANGELOG.md)

## Lizenz
MIT, siehe [LICENSE](LICENSE).