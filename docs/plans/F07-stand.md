# F07 – Übergabe (Stand 2026-10-08)

Nur für die Dauer von F07. Wird in Etappe 8 vor dem Pull Request entfernt. Dauerhaftes steht danach in
ADR 0004, CHANGELOG und AGENT_LOG. Diese Datei enthält keine Zugangsdaten und keine Passwörter.

## a) Ziel, Abnahme, Branch

- Ziel: `search_products`, `get_product` und `find_customer` des Datenservice `hoffmann-data` liefern
  echte Daten aus PostgreSQL und ersetzen die drei Platzhalter. Die übrigen fünf Schnittstellen
  (`create_lead`, `log_activity`, beide Resources, der Prompt) bleiben unverändert Platzhalter.
- Abnahme (Auftrag 3.2): Alle drei liefern Daten aus der Datenbank und sind durch Tests abgedeckt, auch für
  leere Treffer.
- Branch: `feat/f07-read-tools`.
- Commits: `73e4d1b` (Etappe 1 rot), `86b9204` (Etappe 1 grün), `c293e51` (Etappe 2 rot), `5d5d47e`
  (Etappe 2 grün).
- Ausführlicher Plan: `C:\Users\sobek\.claude\plans\pasted-content-id-8b3d-aufgabe-f07-optimized-lemur.md`
  (liegt außerhalb des Repositories und kann in einer neuen Sitzung fehlen; diese Datei ist deshalb in sich
  vollständig).

## b) Etappen

| # | Inhalt | Abnahmekriterium | Stand |
|---|---|---|---|
| 0 | Fragen beantworten, `psycopg` freigeben | Antworten liegen vor, Abhängigkeit freigegeben | erledigt |
| 1 | DB-Fixtures, Rollen-Skript `db/roles/data_service_ro.sql`, `test_role_script.py` | Rolle liest nur (SELECT auf fünf Tabellen mit Policies), Schreibversuche verweigert, Skript zweimal ausführbar, Namensersetzung im Test belegt | erledigt |
| 2 | `dburl.py`, `MCP_SERVER_DATABASE_URL` in Config und `main()`, Paritätstest gegen `guard.py`, `psycopg` in `requirements.txt` | Bisherige Tests unverändert grün, URL-Fälle grün, Meldungen ohne Werte | erledigt (384 Tests grün mit lokaler Datenbank) |
| 3 | Alle Tests der drei Werkzeuge und der Anpassungen schreiben (nur Tests), Rot-Lauf, roter Stand als `test:`-Commit pushen | Rot-Protokoll je Datei mit Grund; jeder Pflichtfall hat mindestens einen Test | offen |
| 4 | `db.py`, `limits.py`, `catalog.py` mit `search_products`, Verdrahtung in `server.py` | Tests zu Suche, Limits und Verbindung grün; Synonym-Ergebnis berichtet (welche Begriffe durchfallen) | offen |
| 5 | `get_product` | Tests grün | offen |
| 6 | `find_customer` in `crm.py` | Alle neuen und alten MCP-Tests grün, fünf Platzhalter unverändert | offen |
| 7 | Startprüfung der Rolle, nur lesend (`verify_read_only_role` in `db.py`, Aufruf in `main()`) | Tests grün, keine Schreib-SQL in der Prüfung, Suite ohne Datenbank bleibt grün | offen (optional zurückstellbar, siehe h) |
| 8 | CI-Diff (Mensch), ADR 0004, README, CHANGELOG, `mcp-server/CLAUDE.md`, `mcp-server/README.md`, DATENMODELL, `.env.example`, Kommentare in `db/schema.sql`; `code-reviewer` und `security-reviewer`; diese Datei entfernen; Pull Request | Pipeline grün (`gh pr checks`), Reviewer-Funde bearbeitet, CHANGELOG- und AGENT_LOG-Eintrag vorhanden | offen |
| 9 | Mensch: Rolle in Supabase einspielen, Passwort setzen, `MCP_SERVER_DATABASE_URL` setzen, Probeaufruf | `search_products "Gurtband"` liefert Daten, Startprüfung besteht, Schreibversuch über die Rolle scheitert | offen |

## c) Nächster Schritt: Etappe 3, Schritt T

Alle Tests schreiben, **keine Implementierung**. Erledigt in Etappe 3 bisher: ein Edit in
`tests/mcp_server/mcp_testkit.py` (`READ_TOOLS`, `PLACEHOLDER_TOOLS`, `PRODUCTS_JSON`).

Noch zu schreiben (je Datei ein Edit zur Freigabe):

1. `mcp_testkit.py` weiter: Hilfen `structured(result)`, `error_text(result)`, `all_keys(value)`,
   `assert_no_internal_ids(value)` (kein Schlüssel `id` oder `*_id`).
2. `conftest.py`: `RoDatabase` (Test-Double mit `connection()`: Savepoint, `SET LOCAL ROLE`, am Ende
   `RESET ROLE` nur bei normalem Ende; gibt die Test-Verbindung heraus), Fixtures `tool_app`, `tool_call`
   (ruft `tools/call` über die ganze App mit Token auf und gibt `result` zurück) und `catalog` (lädt alle
   Artikel aus `data/stammdaten/products.json` über `make._insert` in die Test-Transaktion).
3. `test_db_connection.py`: `db.read_connection(database)`; ohne Datenbank: `NOT_CONFIGURED`; ein
   `ToolError` aus dem Block läuft unverändert durch (nicht verschluckt, nicht umgeschrieben, kein
   WARNING-Eintrag); `psycopg.Error` und unerwartete Ausnahmen aus dem Block oder aus `connection()` werden
   auf `DATABASE_ERROR` abgebildet (`__cause__` ist `None`); **caplog-Test**: genau ein WARNING-Eintrag des
   Loggers `hoffmann_data.db` mit nur Fehlerklasse und SQLSTATE, nie Text der Ausnahme, kein `exc_info`, nie
   Host, Benutzer, Datenbankname, URL. Über die ganze App: unerreichbarer Port (`127.0.0.1:1`) und eine
   nicht vorhandene Datenbank auf dem Test-Server, jeweils ohne Zugangswerte in Antwort, Log, stdout und
   stderr. Echte `Database`: Verbindung pro Aufruf (gezählter `psycopg.connect`, danach geschlossen), nur
   lesend (`INSERT` scheitert mit `ReadOnlySqlTransaction`), `SHOW statement_timeout` ist `5s`,
   `prepare_threshold` ist `None`, `repr` ohne URL, der Konstruktor verbindet nicht.
4. `test_limits.py` (Modul `hoffmann_data/limits.py`): `check_query`, `check_limit`,
   `check_article_number`, `check_customer_query` mit festen Meldungen ohne Eingabewert.
5. `test_search_products.py`, `test_get_product.py`, `test_find_customer.py` mit allen Pflichtfällen aus
   dem Plan (Treffer, kein Treffer, Synonyme aus `products.json`, Groß/Klein, SQL-Metazeichen, zu lange und
   leere Eingabe, Limit-Grenzen, unbekannte Artikelnummer, unbekannter Kunde, Kunde mit und ohne
   Aktivitäten, kein Geheimnis, keine internen IDs).
6. **Messtest `test_limit_type_coercion`**: Abweichung vom ersten Plan. Vor der Implementierung kann kein
   Lauf das Pydantic-Verhalten zeigen (die Platzhalter haben keine Parameter). Deshalb schreiben wir den
   **gewünschten** Test: `limit` als `"7"`, `true`, `7.0`, `7.5`, `"abc"`, `null` und `query` als `123`,
   `true`, `["a"]` werden mit fester Meldung abgelehnt, ohne Echo des Wertes (Marker `GEHEIM-4711`). In
   Etappe 4 wird zuerst die einfache Signatur `limit: int = 5` gebaut. Rutscht etwas durch oder erscheint der
   Wert in der Meldung, wechselt die Signatur auf `Annotated[Any, WithJsonSchema({...})]` mit strenger
   Prüfung in `limits.py` (`type(value) is int`).
7. `test_mcp_interfaces.py`: Platzhaltertest nur noch für `PLACEHOLDER_TOOLS`; Echo-Test auf `log_activity`;
   neue Tests: Lese-Werkzeuge ohne „Platzhalter“ in der Beschreibung, `inputSchema` (`query` und
   `article_number` Pflicht, `limit` optional), `outputSchema` vorhanden (Annahme, wird in Etappe 4 belegt);
   Docstring „Alle Platzhalter sind parameterlos“ anpassen.
8. `test_mcp_no_leak.py`: `test_package_has_no_database_access` auf die AST-Prüfung umbauen und den
   Docstring aktualisieren. Es gilt: nur `hoffmann_data/db.py` darf `psycopg` importieren (Prüfung der
   Importknoten, nicht des Textes), `db.py` importiert es tatsächlich, `\bDATABASE_URL\b` bleibt verboten,
   `asyncpg` bleibt verboten.
9. Neu `test_package_sql_rules.py`: in den Zeichenketten des Pakets (Docstrings ausgenommen) kein
   `INSERT|UPDATE|DELETE|DROP|ALTER|TRUNCATE|GRANT|COPY|CREATE|MERGE`; keine f-Strings und keine
   `%`-/`.format`-Formatierung auf SQL-Konstanten (nur `psycopg.sql.SQL(...).format` für feste Namen).

Danach R: Der Mensch führt den Rot-Lauf aus (Befehl nennen, nicht ausführen), prüft jeden Fehlschlag auf
den erwarteten Grund, und pusht den roten Stand als eigenen `test:`-Commit
(z. B. `test(mcp): add failing tests for read tools (F07)`).

### Geplante Schnittstellen (die Tests legen sie fest)

- `hoffmann_data.db`: `Database(url, **connect_kwargs)` mit `connection()` (Kontextmanager; öffnet bei
  jedem Aufruf neu mit `psycopg.connect(url, connect_timeout=5, prepare_threshold=None, ...)`, setzt
  `read_only = True` und `SET LOCAL statement_timeout = '5s'`, schließt am Ende, committet nie). `import
  psycopg` und Aufruf als `psycopg.connect`, damit der Zähl-Test greift. `read_connection(database)`
  (Kontextmanager; `None` gibt `NOT_CONFIGURED`; bildet `psycopg.Error` und unerwartete `Exception` auf
  `DATABASE_ERROR` ab, `ToolError` geht unverändert durch). Konstanten `NOT_CONFIGURED`
  (`"Die Datenbank ist nicht konfiguriert."`) und `DATABASE_ERROR`. Logger `hoffmann_data.db`.
- `create_mcp_server(database=None)` und `create_app(config, database=None)`; ohne Argument baut
  `create_app` `Database(config.database_url)`, wenn eine URL gesetzt ist.
- `hoffmann_data.limits`: `MAX_QUERY_LENGTH = 200`, `DEFAULT_LIMIT = 5`, `MAX_LIMIT = 20`; Meldungen
  `QUERY_INVALID`, `LIMIT_INVALID`, `ARTICLE_NUMBER_INVALID`, `CUSTOMER_QUERY_INVALID`.
- Ergebnisformen (`structuredContent`):
  - `search_products` → `{"items": [{article_number, name, category, is_active}], "count": n}`.
  - `get_product` → `{"product": {article_number, name, category, description, technical_data,
    list_price, price_unit, lead_time_days, is_active, fits_assemblies: [{article_number, name, note}],
    compatible_parts: [{article_number, name, note}]} | null}`.
  - `find_customer` → `{"customer": {company_name, domain, industry, country, status, contacts: [{first_name,
    last_name, email, job_title, language}], recent_activities: [{type, occurred_at, subject, summary,
    amount_eur, created_by, article_number}]} | null, "matched_by": "company"|"email"|"domain"|null}`.
    Kein Feld `ambiguous`.
- `hoffmann_data.crm.pick_unique(rows)`: keine Zeile → `None`, eine → diese, mehr als eine →
  `ToolError("Mehrdeutiger Treffer.")` (Konstante `AMBIGUOUS_MESSAGE`).

## d) Verbindliche Entscheidungen

- **Sync-Tools** (`def`, nicht `async def`): Das SDK führt sie in einem Worker-Thread aus
  (`func_metadata.py`, `call_fn`). Synchrones psycopg, kein asyncpg.
- **Rolle `data_service_ro`**: nur `SELECT` auf `products`, `product_fits`, `customers`, `contacts`,
  `activities`, je eine `SELECT`-Policy `ro_select`, kein `BYPASSRLS`, `discount_rules` nicht lesbar (kommt
  mit F09). Das Skript steht ohne Passwort im Repository, der Mensch führt es manuell aus. Nach jedem
  `python -m db.seed` erneut ausführen (der Reset löscht Rechte und Policies); der Seed-Code bleibt
  unverändert, der Punkt kommt in das Issue für zurückgestellte Punkte.
- **Verbindung** pro Anfrage, kein Pool. `MCP_SERVER_DATABASE_URL` nur aus der Umgebung, gleiche
  `sslmode`-Regeln wie `db/seed/guard.py`; Fehlermeldungen nennen nie Werte.
- **Suche**: Volltext `german` über Name und Beschreibung (`websearch_to_tsquery`), dazu Artikelnummer exakt
  und als Präfix (`starts_with`, nicht `LIKE`). Nur UND-Verknüpfung, kein ODER-Fallback. Keine Extension,
  keine Schemaänderung, kein Index (ein GIN-Index wäre ein eigener Schritt mit eigenem PR).
  Reihenfolge: exakt, Präfix, Rang, dann `article_number`; inaktive Artikel gekennzeichnet
  (`is_active: false`) und hinter den aktiven.
- **Grenzen**: Suchtext 1 bis 200 Zeichen (nach `strip()`), keine Steuerzeichen; `limit` Standard 5,
  1 bis 20; ungültige Eingabe gibt einen `ToolError` mit fester Meldung ohne Eingabewert. Artikelnummer
  nach `strip()` und Großschreibung im Format des CHECK in `schema.sql`.
- **Rückgabe**: Nichttreffer ist `null` (`get_product`, `find_customer`) beziehungsweise eine leere Liste
  (`search_products`), kein Fehler; Fehler sind ungültige Eingabe und Datenbankprobleme. Preise und alle
  Geldbeträge (`list_price`, `amount_eur`) als String mit zwei Nachkommastellen (`"890.00"`). Keine internen
  IDs in Ausgaben; Verweise über natürliche Schlüssel (`article_number`, `domain`, `email`).
- **find_customer**: Eingabe mit `@`: Kontakt über `email`, sonst Kunde über die Domain der E-Mail
  (`matched_by` `email` beziehungsweise `domain`); ohne `@`: `lower(company_name)` (`company`), sonst, wenn es
  wie eine Domain aussieht, `domain`. Immer exakt, Vergleiche mit `lower()` in PostgreSQL, Subdomains
  werden nicht gematcht. Bis 20 Kontakte und die letzten 10 Aktivitäten (neueste zuerst). Mehrdeutigkeit
  (heute per UNIQUE-Constraint auf `domain`, `email` und `lower(company_name)` unmöglich) ist ein fester
  `ToolError("Mehrdeutiger Treffer.")`, nie Raten. Keine Dublettenlogik (das ist F08).
- **Fehlerbehandlung in `db.py`**: fängt nur `psycopg`-Fehler und unerwartete Ausnahmen der
  Datenbankabfrage; ein `ToolError` aus der Eingabeprüfung wird nicht verschluckt oder umgeschrieben (ein
  Test dafür). Bei Datenbankfehlern loggt der Dienst auf WARNING nur Fehlerklasse und SQLSTATE, nie den
  Text, nie Host oder Benutzer.
- **Startprüfung** (Etappe 7): nur lesende Abfragen (`pg_roles`: `rolsuper`, `rolbypassrls`;
  `has_table_privilege` für INSERT, UPDATE, DELETE, TRUNCATE auf die fünf Tabellen), kein echter
  Schreibversuch, **kein Abschalter**; Anpassung nach dem Probelauf in Etappe 9.
- **Abhängigkeit** `psycopg[binary]==3.3.6` in `mcp-server/requirements.txt` ist eingetragen und freigegeben.
  Nur `db.py` darf `psycopg` importieren.
- **Lokale Datei** `db/schema.sql`: In Etappe 8 werden nur die Kommentare (Z. 4 und 136–138) angepasst. Kein
  Test legt Inhalt oder Hash der Datei fest; `test_seed_run.py` entfernt `--`-Kommentare und prüft den Code.

## e) Arbeitsabsprachen

- Ablauf je Etappe: **T** (Claude schreibt die Tests) → **R** (der Mensch führt den Rot-Lauf aus und zeigt
  die Ausgabe) → **I** (Claude implementiert) → **G** (grüner Lauf durch den Menschen).
- Claude führt **keine Befehle** aus und nennt sie nur, auch keine lesenden in der Shell. Lesen mit
  Read/Grep/Glob ist erlaubt.
- Edits einzeln zur Freigabe, nie im Auto-Modus.
- Commits und Push macht der Mensch; Claude zeigt vorher den Diff. Nie auf `main` pushen, nie selbst
  mergen, Pull Request erst, wenn alles grün ist.
- Der rote Stand jeder Etappe wird gepusht, damit der Chat-Assistent die Tests vor der Implementierung
  liest.
- PowerShell-Befehle immer einzeln und kopierbar nennen.
- Passwörter und `.env`-Dateien nie lesen oder ausgeben; keine `.env` im Projekt anlegen.
- Fehler von Claude gehören sofort in `AGENT_LOG.md`.

## f) Lokale Testumgebung (ohne Passwort)

- PostgreSQL 17 lokal als Windows-Dienst `postgresql-x64-17`, lauscht nur auf localhost (Port 5432).
- `TEST_DATABASE_URL` setzt der Mensch nur pro PowerShell-Sitzung und entfernt sie danach
  (`Remove-Item Env:TEST_DATABASE_URL`). Der Benutzer braucht `CREATEROLE` für die Rollen-Tests.
- Ohne die Variable werden die Tests mit Datenbank lokal übersprungen. In GitHub Actions sind sie ein
  Fehler, damit die Pipeline nie grün ist, ohne dass sie lief. Ist die Variable gesetzt, die Datenbank aber
  nicht erreichbar, ist das ein Fehler (`connect_timeout=5`, feste Meldung), kein Skip.

## g) Erkenntnisse und Fallen

- `EXPECTED_ROLE_NAME_COUNT` ist **13** (drei `ALTER ROLE`, nicht zwei); die Aufstellung steht in
  `mcp_testkit.py`. Der Rollenname darf in Kommentaren und Policy-Namen des Skripts nicht vorkommen.
- `UPDATE` auf Identity-Spalten (`GENERATED ALWAYS`) nur mit `SET id = DEFAULT` testen; `SET id = id`
  liefert den Identity-Fehler vor der Rechteprüfung.
- `\bDATABASE_URL\b` statt eines Lookbehinds: Der Lookbehind traf `DATABASE_URL_VAR`.
- Die URL-Tabellen in `mcp_testkit.py` müssen jeden Ablehnungsgrund aus `db/seed/guard.py` abdecken (der
  Review fand elf fehlende Fälle).
- Ein Test, der vor der Implementierung grün ist, ist verdächtig (der repr-Test der Config lief leer durch;
  seither prüft er zuerst `database_url`).
- Die `main()`-Tests brauchen in Etappe 7 einen Monkeypatch der Startprüfung, sonst verbindet `main()` mit
  der Datenbank.
- Der Format-Hook formatiert Dateien nach dem Edit um; bei dem nächsten Edit in einer geänderten Region
  zuerst lesen.
- `.github/` und `.claude/` ändert nur der Mensch (CI-Diff liefert Claude als Text).
- Die Meldung zu „Erwartetes Rot“ stimmte in Etappe 2 nicht ganz: Rot-Erwartungen nur nennen, wenn sie
  einzeln geprüft sind.

## h) Offen für später

- Etappe 7 (Startprüfung) kann zurückgestellt und als Issue geführt werden.
- Issue-Text „Seed soll Rollen-Skript anwenden“ für den Menschen (Etappe 8): `DROP TABLE` löscht Rechte und
  Policies, aktueller Workaround (Skript nach jedem Seed erneut ausführen), Vorschlag, betroffene Dateien
  `db/seed/writer.py` und `db/roles/data_service_ro.sql`. Claude legt kein Issue an.
- Etappe 9: Supabase-Schritte (Rolle einspielen, Passwort separat setzen, `MCP_SERVER_DATABASE_URL`
  setzen, Probeaufruf, Prüfung von Pooler und Rollenname mit Projektkennung).
- `ci.yml`-Diff für den Job `mcp-tests` (Postgres-17-Dienst und `TEST_DATABASE_URL` auf localhost:5432,
  Aufbau wie im Job `schema-tests`; der Mensch wendet ihn an).
- Der Pull Request erst danach, mit `code-reviewer` und `security-reviewer` und grüner Pipeline.
- F08: Freigabe-Mechanismus für `create_lead` (technisch, nicht nur Beschreibungstext), getrennte Token für
  Lesen und Schreiben, Prüfungen 2 und 3 aus DATENMODELL §5 auch in den Schreib-Werkzeugen.
