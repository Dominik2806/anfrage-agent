# F07 – Übergabe (Stand 2026-10-09)

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
  (Etappe 2 grün), `dd5f834` (Etappe 3 rot), `1f69995` und `4281137` (Etappe 3 Nachbesserungen), `82b4bfc`
  (Etappe 4 grün), `0636258` (Etappe 5 grün), `3e80c5d` (Etappe 6 grün), `4d1f8b5` (Etappe 7 rot), `dfe3d0e`
  (Etappe 7: Test-Regel für `CREATE` und Lint) und `ab6f85d` (Etappe 7 grün).
- Der ausführliche Plan der ersten Sitzung lag außerhalb des Repositories und kann in einer neuen Sitzung
  fehlen; diese Datei ist deshalb in sich vollständig.

## b) Etappen

| # | Inhalt | Abnahmekriterium | Stand |
|---|---|---|---|
| 0 | Fragen beantworten, `psycopg` freigeben | Antworten liegen vor, Abhängigkeit freigegeben | erledigt |
| 1 | DB-Fixtures, Rollen-Skript `db/roles/data_service_ro.sql`, `test_role_script.py` | Rolle liest nur (SELECT auf fünf Tabellen mit Policies), Schreibversuche verweigert, Skript zweimal ausführbar, Namensersetzung im Test belegt | erledigt |
| 2 | `dburl.py`, `MCP_SERVER_DATABASE_URL` in Config und `main()`, Paritätstest gegen `guard.py`, `psycopg` in `requirements.txt` | Bisherige Tests unverändert grün, URL-Fälle grün, Meldungen ohne Werte | erledigt (384 Tests grün mit lokaler Datenbank) |
| 3 | Alle Tests der drei Werkzeuge und der Anpassungen schreiben (nur Tests), Rot-Lauf, roter Stand als `test:`-Commit pushen | Rot-Protokoll je Datei mit Grund; jeder Pflichtfall hat mindestens einen Test | erledigt (`dd5f834` rot, `1f69995` und `4281137` Nachbesserungen) |
| 4 | `db.py`, `limits.py`, `catalog.py` mit `search_products`, Verdrahtung in `server.py`, `pydantic` gepinnt | Tests zu Suche, Limits und Verbindung grün; Synonym-Ergebnis berichtet | erledigt (`82b4bfc`; 723 bestanden, 147 rot, alle zu `get_product` und `find_customer`; `ruff format` und `ruff check mcp-server` sauber) |
| 5 | `get_product` | `test_get_product.py` und die `get_product`-Fälle in `test_mcp_interfaces.py` und `test_db_connection.py` grün | erledigt (`0636258`; 777 bestanden, 93 rot, alle zu `find_customer`: 88 + 4 + 1; `ruff format` und `ruff check mcp-server` sauber) |
| 6 | `find_customer` in `crm.py` | Alle neuen und alten MCP-Tests grün, fünf Platzhalter unverändert | erledigt (`3e80c5d`; 870 bestanden, 0 rot; ruff sauber) |
| 7 | Startprüfung der Rolle, nur lesend (`verify_read_only_role` in `db.py`, Aufruf in `main()`) | Tests grün, keine Schreib-SQL in der Prüfung, Suite ohne Datenbank bleibt grün | erledigt (`4d1f8b5` Tests rot, `dfe3d0e` Test-Regel und Lint, `ab6f85d` grün; alle Tests grün, siehe c) |
| 8 | CI-Diff (Mensch), ADR 0004, README, CHANGELOG, `mcp-server/CLAUDE.md`, `mcp-server/README.md`, DATENMODELL, `.env.example`, Kommentare in `db/schema.sql`; `code-reviewer` und `security-reviewer`; diese Datei entfernen; Pull Request | Pipeline grün (`gh pr checks`), Reviewer-Funde bearbeitet, CHANGELOG- und AGENT_LOG-Eintrag vorhanden | offen |
| 9 | Mensch: Rolle in Supabase einspielen, Passwort setzen, `MCP_SERVER_DATABASE_URL` setzen, Probeaufruf | `search_products "Gurtband"` liefert Daten, Startprüfung besteht, Schreibversuch über die Rolle scheitert | offen |

## c) Nächster Schritt: Etappe 8 (Doku, Reviews, Pull Request)

Etappe 1 bis 7 sind erledigt. Alle drei Lese-Werkzeuge und die Startprüfung der Rolle sind umgesetzt, alle Tests
in `tests/mcp_server` sind grün (Lauf des Menschen). Offen sind Etappe 8 und 9 (Tabelle in b):
- **Etappe 8:** CI-Diff für den Job `mcp-tests` (Claude liefert ihn als Text, der Mensch wendet ihn an, siehe h),
  ADR 0004 (Lehren aus Abschnitt i), README, CHANGELOG, `mcp-server/CLAUDE.md` (Aufbau um `limits.py`, `db.py`,
  `catalog.py` und `crm.py` ergänzen; die Regel „Im Paket kein `psycopg`“ gilt nur noch außerhalb von `db.py`),
  `mcp-server/README.md`, DATENMODELL, `.env.example`, Kommentare in `db/schema.sql`; `code-reviewer` und
  `security-reviewer` mit dem Diff; AGENT_LOG-Eintrag; diese Datei entfernen; Pull Request erst bei grüner Pipeline.
- **Etappe 9:** Schritte des Menschen in Supabase (siehe h).
Vor jeder Etappe zuerst der Plan, dann Edits einzeln zur Freigabe, dann der Lauf des Menschen.

### Startprüfung der Rolle (Etappe 7, umgesetzt)
Tests: `tests/mcp_server/test_role_check.py` (66 Fälle); ein bestehender `main()`-Test in
`test_mcp_config_database.py` ersetzt die Prüfung per `monkeypatch` (`raising=False`).
- `db.verify_read_only_role(database)`: öffnet genau einmal `database.connection()`, führt nur lesende
  Abfragen aus (zwei feste Konstanten `ROLE_SQL` und `TABLES_SQL`, beide beginnen mit `SELECT`), liest die Rolle
  `current_user` (nicht `session_user`) und gibt `None` zurück oder wirft `RoleCheckError`.
- `db.RoleCheckError(failed)`: Attribut `failed` (Tupel der Kurzbezeichnungen); der Text besteht nur aus
  „Prüfung der Datenbankrolle fehlgeschlagen: “ und den Kurzbezeichnungen, nie Rollenname, Host, Benutzer,
  Datenbankname, URL oder Passwort; kein `__cause__`.
- Kurzbezeichnungen in fester Reihenfolge (Tabellen: `products`, `product_fits`, `customers`, `contacts`,
  `activities`): 1 `rolle-ist-superuser`; 2 `rolle-umgeht-rls`; 3 `schreibrecht:<RECHT>:<tabelle>` für INSERT,
  UPDATE, DELETE, TRUNCATE (INSERT und UPDATE auch als Spaltenrecht über `has_any_column_privilege`); 4
  `kein-select:<tabelle>`; 5 `create-im-schema` (`has_schema_privilege` auf `current_schema()`); 6
  `tabelle-fehlt:<tabelle>` (nur diese Kurzbezeichnung, nicht zusätzlich 3 oder 4; Auflösung mit `to_regclass`
  über den `search_path`). Dazu `datenbankfehler`, wenn Verbindung oder Abfrage scheitern: genau ein WARNING von
  `hoffmann_data.db` mit Klasse und SQLSTATE, ohne Text und ohne Traceback.
- `main()` ruft `db.verify_read_only_role(db.Database(config.database_url))` **über das Modul `db`** auf (damit
  `monkeypatch` greift), nach `load_config(require_database=True)` und vor `uvicorn.run`. Bei `RoleCheckError`:
  der Text auf stderr, Exit-Code 1, kein Start. Ohne URL bleibt der bisherige Abbruch (Exit 1), die Prüfung wird
  dann nicht aufgerufen. Es gibt keinen Schalter und keine zusätzliche Umgebungsvariable (AST-Test).

### Bereits umgesetzt (Etappe 4 bis 7)
- `limits.py`: `check_query`, `check_limit`, `check_article_number`, `check_customer_query` mit festen Meldungen.
- `db.py`: `Database`, `read_connection`, `fetch_all(connection, query, params)` (liefert Dicts),
  `NOT_CONFIGURED`, `DATABASE_ERROR`, `ConnectionSource`; seit Etappe 7 `RoleCheckError`,
  `verify_read_only_role`, `ROLE_SQL`, `TABLES_SQL`, `CHECKED_TABLES`, `NO_SELECT_FAILURES`.
- `catalog.py`: `search_products` (`SEARCH_SQL`, `ProductHit`, `SearchResult`) und `get_product`
  (`GET_PRODUCT_SQL`, eine Abfrage mit beiden Richtungen von `product_fits` als JSON-Listen, `LinkedProduct`,
  `ProductDetail`, `ProductResult`, Preis mit `f"{value:.2f}"`).
- `crm.py`: `find_customer`, `pick_unique`, `AMBIGUOUS_MESSAGE`; je Weg (E-Mail, Domain, Firma) eine feste Abfrage
  mit `LIMIT 2` und `pick_unique`, `CONTACTS_SQL` (gefundener Kontakt zuerst, höchstens 20) und `ACTIVITIES_SQL`
  (neueste zuerst, höchstens 10, `LEFT JOIN products`); `occurred_at` in UTC als ISO-Text, Beträge mit
  `f"{value:.2f}"`.
- `server.py`: `create_mcp_server(database=None)`, `create_app(config, database=None)`, `search_products`,
  `get_product` und `find_customer` mit `Annotated[Any, WithJsonSchema(...)]`. Platzhalter bleiben
  `create_lead`, `log_activity`, beide Resources und der Prompt.
- `__main__.py`: Aufruf der Startprüfung (siehe oben).

### Schnittstellen der Werkzeuge (alle drei umgesetzt)
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
  Reihenfolge: exakter Treffer zuerst (auch wenn der Artikel inaktiv ist), dann aktive vor inaktiven, darin
  Präfix, dann Volltext nach Rang, dann `article_number`. Inaktive Artikel sind mit `is_active: false`
  gekennzeichnet. Kleinschreibung der Artikelnummer wird zu Großbuchstaben normalisiert.
- **Grenzen** (`limits.py`): Suchtext und Kundenangabe 1 bis 200 Zeichen nach `strip()`; abgelehnt wird jedes
  Zeichen der Unicode-Kategorien C* (Cc, Cf, Cs, Co, Cn) sowie Zl und Zp. `limit` nur als echtes `int`, Standard
  5, 1 bis 20. Artikelnummer: nach `strip()` reines ASCII, dann `upper()`, dann `fullmatch` im Format des CHECK
  in `schema.sql`. Ungültige Eingabe gibt einen `ToolError` mit fester Meldung ohne Eingabewert; `null` und
  falsche Typen gelten für alle Textparameter gleich.
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
  Datenbankabfrage (`except Exception` mit `noqa: BLE001`, Absicht); ein `ToolError` aus der Eingabeprüfung wird
  nicht verschluckt oder umgeschrieben (ein Test dafür). Bei Datenbankfehlern loggt der Dienst auf WARNING nur
  Fehlerklasse und SQLSTATE, nie den Text, nie Host oder Benutzer.
- **Startprüfung** (Etappe 7, umgesetzt): nur lesende Abfragen (`pg_roles`: `rolsuper`, `rolbypassrls`;
  `has_table_privilege` und `has_any_column_privilege` für INSERT, UPDATE, DELETE, TRUNCATE auf die fünf
  Tabellen, `has_schema_privilege` für CREATE), kein echter Schreibversuch, **kein Abschalter**; Anpassung nach
  dem Probelauf in Etappe 9. `test_package_sql_rules.py` erlaubt in `db.py` die Rechtenamen INSERT, UPDATE,
  DELETE, TRUNCATE, CREATE und die Kurzbezeichnung `create-im-schema` nur als exakt gleiche Zeichenketten.
  Einzelheiten in c.
- **Abhängigkeiten** in `mcp-server/requirements.txt`: `psycopg[binary]==3.3.6` und `pydantic==2.13.5`
  (explizit gepinnt, weil `server.py` `WithJsonSchema` direkt importiert); `pydantic_core` kommt mit pydantic,
  `typing_extensions` wird nicht verwendet. Nur `db.py` darf `psycopg` importieren.
- **Lokale Datei** `db/schema.sql`: In Etappe 8 werden nur die Kommentare (Z. 4 und 136–138) angepasst. Kein
  Test legt Inhalt oder Hash der Datei fest; `test_seed_run.py` entfernt `--`-Kommentare und prüft den Code.

## e) Arbeitsabsprachen

- Ablauf je Etappe mit Tests: **T** (Claude schreibt die Tests) → **R** (der Mensch führt den Rot-Lauf aus und
  zeigt die Ausgabe) → **I** (Claude implementiert) → **G** (grüner Lauf durch den Menschen). Für Etappe 5
  und 6 gibt es keinen Schritt T und R mehr (Tests liegen), nur Plan, I und G.
- Claude führt **keine Befehle** aus und nennt sie nur, auch keine lesenden in der Shell. Lesen mit
  Read/Grep/Glob ist erlaubt.
- Edits einzeln zur Freigabe, nie im Auto-Modus.
- Commits und Push macht der Mensch; Claude zeigt vorher den Diff. Nie auf `main` pushen, nie selbst
  mergen, Pull Request erst, wenn alles grün ist.
- Der rote Stand jeder Etappe mit Tests wird gepusht, damit der Chat-Assistent die Tests vor der
  Implementierung liest.
- PowerShell-Befehle immer einzeln und kopierbar nennen.
- Passwörter und `.env`-Dateien nie lesen oder ausgeben; keine `.env` im Projekt anlegen.
- Fehler von Claude gehören sofort in `AGENT_LOG.md`.
- Sonderzeichen im Quelltext nur als Escapes (achtstellig mit `\U`); die Schreibwerkzeuge wandeln die kurze
  Form in das wörtliche Zeichen um. Nach dem Schreiben mit einer Suche prüfen.

## f) Lokale Testumgebung (ohne Passwort)

- PostgreSQL 17 lokal als Windows-Dienst `postgresql-x64-17`, lauscht nur auf localhost (Port 5432).
- `TEST_DATABASE_URL` ist in der Benutzer-Umgebung des Menschen gesetzt (hier kein Wert). In einem neuen
  Fenster muss sie geladen sein; sonst werden die Tests mit Datenbank übersprungen und ein Lauf sagt nichts.
  Der Benutzer braucht `CREATEROLE` für die Rollen-Tests.
- Ohne die Variable werden die Tests mit Datenbank lokal übersprungen. In GitHub Actions sind sie ein
  Fehler, damit die Pipeline nie grün ist, ohne dass sie lief. Ist die Variable gesetzt, die Datenbank aber
  nicht erreichbar, ist das ein Fehler (`connect_timeout=5`, feste Meldung), kein Skip.
- Python 3.13 (lokal und in CI).

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
- Die `main()`-Tests mit gültiger URL brauchen einen Monkeypatch der Startprüfung, sonst verbindet `main()` mit
  der Datenbank (umgesetzt in Etappe 7 für `test_start_with_valid_database_url_serves_once`, mit
  `raising=False`).
- Der Format-Hook formatiert Dateien nach dem Edit um; bei dem nächsten Edit in einer geänderten Region
  zuerst lesen.
- `.github/` und `.claude/` ändert nur der Mensch (CI-Diff liefert Claude als Text).
- Die Meldung zu „Erwartetes Rot“ stimmte in Etappe 2 nicht ganz: Rot-Erwartungen nur nennen, wenn sie
  einzeln geprüft sind.

## h) Offen für später

- Etappe 7 (Startprüfung) ist umgesetzt; offen bleibt nur die Anpassung nach dem Probestart in Etappe 9.
- Issue-Text „Seed soll Rollen-Skript anwenden“ für den Menschen (Etappe 8): `DROP TABLE` löscht Rechte und
  Policies, aktueller Workaround (Skript nach jedem Seed erneut ausführen), Vorschlag, betroffene Dateien
  `db/seed/writer.py` und `db/roles/data_service_ro.sql`. Claude legt kein Issue an.
- Issue-Text „Fehlender Pflichtparameter: SDK-Meldung nennt das Argument-Dict“ (siehe i): nicht ohne Eingriff
  ins SDK abstellbar; Vorschlag und Folgen für den Menschen aufschreiben. Claude legt kein Issue an.
- Etappe 9: Supabase-Schritte (Rolle einspielen, Passwort separat setzen, `MCP_SERVER_DATABASE_URL`
  setzen, Probeaufruf, Prüfung von Pooler und Rollenname mit Projektkennung).
- `ci.yml`-Diff für den Job `mcp-tests` (Postgres-17-Dienst und `TEST_DATABASE_URL` auf localhost:5432,
  Aufbau wie im Job `schema-tests`; der Mensch wendet ihn an). In CI prüfen, ob die Suchtests mit Umlauten in
  Großschreibung bestehen (siehe i, Locale).
- Der Pull Request erst danach, mit `code-reviewer` und `security-reviewer` und grüner Pipeline.
- Offener Punkt `is_active` in den Verweisen von `get_product` (`fits_assemblies`, `compatible_parts`): Heute
  ohne (siehe i); aufnehmen heißt `LINK_KEYS` im Test ändern, das macht der Mensch.
- F08: Freigabe-Mechanismus für `create_lead` (technisch, nicht nur Beschreibungstext), getrennte Token für
  Lesen und Schreiben, Prüfungen 2 und 3 aus DATENMODELL §5 auch in den Schreib-Werkzeugen.

## i) Gelernte Punkte für ADR 0004 und den Bericht (Etappe 3 bis 7)

- **Abhängigkeiten:** `pydantic==2.13.5` ist explizit gepinnt; kein `typing_extensions`, Ergebnistypen sind
  `typing.TypedDict` (Python 3.13).
- **Eigene Typprüfung:** `query` und `limit` sind `Annotated[Any, WithJsonSchema(...)]` mit eigener Prüfung in
  `limits.py`. Grund: Das SDK gibt bei Pydantic-Meldungen den `input_value` an den Client zurück
  (`tools/base.py:153-156`); die einfache Signatur (`limit: int`, `query: str`) liefert dann keine feste Meldung
  ohne Echo. Dasselbe gilt für `article_number` und die Kundenangabe in Etappe 5 und 6.
- **JSON-Vorparser des SDKs** (`func_metadata.py:254-266`): Er ersetzt nur Texte, die als JSON `null`, eine Liste
  oder ein Objekt gelesen werden (`null`, `[]`, `{}`); sie werden abgelehnt. `true` und `false` ersetzt er nicht
  (`bool` ist ein `int`, Zeile 261), der Text bleibt erhalten.
- **Fehlender Pflichtparameter:** Die SDK-Meldung nennt das Argument-Dict (Issue später, siehe h).
- **`art` in Python:** Die Großschreibung der Artikelnummer in der Suche wird in Python berechnet (nur bei
  ASCII), nicht mit `upper()` in SQL: Das hängt vom Locale der Datenbank ab und machte aus `ſb-1001`
  `SB-1001`.
- **Einzelnes Surrogat** (`"a\ud800b"`): Der Transport weist es ab (HTTP 400, JSON-RPC -32700, Parse error).
  `limits.py` lehnt Kategorie Cs trotzdem ab und bleibt Pflicht, weil der Schutz nicht vom Transportpfad
  abhängen darf.
- **Betriebssystem:** Windows lehnt einen geschlossenen Loopback-Port nicht sofort ab (`ConnectionTimeout` nach
  5 Sekunden, der Test dauert etwa 5,5 Sekunden); Linux liefert `OperationalError`. Der Test akzeptiert beides.
- **Locale:** Umlaute in Großschreibung (Volltext, `lower()`) hängen vom Locale der Datenbank ab. Lokal und im
  Prototyp erfüllt, in CI zu prüfen.
- **Sonderzeichen im Quelltext:** `ruff check` (PLE2502, PLE2515) fand wörtliche Steuer- und Formatzeichen in
  `test_limits.py`. Escapes immer achtstellig mit `\U`, danach mit Suche prüfen (siehe AGENT_LOG).
- **`noqa: BLE001`** in `db.py` ist Absicht: Auch unerwartete Ausnahmen dürfen keinen Text nach außen tragen.
- **Verweise ohne `is_active`:** Die Beschreibung von `get_product` warnt, dass Teile in `compatible_parts`
  ausgelaufen sein können (Status mit `get_product` prüfen, Notiz beachten). Die Verweise haben bewusst kein
  `is_active`, weil `LINK_KEYS` im Test `{article_number, name, note}` ist. Offener Punkt: `is_active` in die
  Verweise aufnehmen; das braucht eine Testanpassung durch den Menschen (siehe h).
- **Paketregel und `CREATE` (Etappe 7):** `test_package_sql_rules.py` erlaubte in `db.py` nur die exakten
  Zeichenketten INSERT, UPDATE, DELETE und TRUNCATE. Die Startprüfung braucht zusätzlich `CREATE` (Rechtename für
  `has_schema_privilege`) und die Kurzbezeichnung `create-im-schema`; beide stehen jetzt als exakte Ausnahme für
  `db.py` in der Regel (Commit `dfe3d0e`). Die Regel bleibt sonst unverändert: kein Wort in SQL-Sätzen, keine
  Schreib-SQL.
- **`SQL_LIKE`-Heuristik:** Die Regel hält jede Zeichenkette mit dem Wort `select` für SQL, also auch den festen
  Teil `kein-select:` in `f"kein-select:{...}"`, und meldet den f-String. Deshalb stehen die Kurzbezeichnungen
  dafür als feste Literale in `NO_SELECT_FAILURES` (Tabellenname → Kurzbezeichnung), keine Zeichenkette mit
  `select` wird zusammengesetzt.
- **Rechtenamen nur als Parameter:** INSERT, UPDATE, DELETE, TRUNCATE, SELECT und CREATE stehen in `db.py` als
  eigene Zeichenketten und gehen nur als Parameter an die Abfragen (`%(priv_insert)s::text` usw.), nie im
  SQL-Text. Auch Parameternamen und Aliase vermeiden verbotene Wörter als eigenes Wort (`priv_create`,
  `can_update`; der Unterstrich gehört zum Wort).
- **Superuser nötig für zwei Testgruppen:** Die Fälle für den Superuser und für `BYPASSRLS` brauchen einen
  Superuser als Testbenutzer (`BYPASSRLS` vergeben darf nur ein Superuser). Lokal werden sie übersprungen, in
  GitHub Actions sind sie ein Fehler (der Dienstbenutzer `postgres` ist dort Superuser).
- **Einmaliger `DeadlockDetected`:** Im ersten Gesamtlauf nach Etappe 7 trat einmal ein `DeadlockDetected` in
  `test_role_script.py` auf; er war danach nicht mehr reproduzierbar. Ursache nicht geklärt. Bei Wiederholung
  die beteiligten Anweisungen aus dem Serverlog der Test-Datenbank festhalten (SQLSTATE 40P01).
- **Für Etappe 9 (Supabase-Probestart):** Bei PostgreSQL bis Version 14 hat PUBLIC das Recht `CREATE` auf dem
  Schema `public`. Dann könnte `create-im-schema` den Start verhindern, obwohl die Rolle selbst nichts erhalten
  hat. Der Probestart zeigt es; über die Abhilfe entscheidet der Mensch (nicht die Prüfung lockern).
