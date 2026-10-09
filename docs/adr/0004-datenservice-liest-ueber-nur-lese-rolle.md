# ADR 0004: Datenservice liest über eine Nur-Lese-Rolle

- Status: Angenommen
- Datum: 2026-10-09

## Kontext
Der Agent darf Katalog, Preise, Lieferzeiten und CRM nur lesen und nichts löschen (Auftrag 5, 5.1). Das ist technisch
durchzusetzen, ein Prompt allein genügt nicht. Mit F07 bekommt der Datenservice „hoffmann-data“ seine ersten Werkzeuge mit
Datenbankzugriff (`search_products`, `get_product`, `find_customer`, Auftrag 6.4). Vor F07 ging das Datenmodell von einer
Serverrolle aus, die Row Level Security umgeht (DATENMODELL Abschnitt 1, Kommentar in `db/schema.sql`). Eine solche Rolle
könnte auch schreiben, ein Fehler im Code oder in einer Eingabe träfe die Daten.

## Entscheidung
1. Der Datenservice verbindet sich als Rolle `data_service_ro`. Das Skript `db/roles/data_service_ro.sql` legt sie an (ohne
   Passwort, vom Menschen ausgeführt, wiederholbar): `LOGIN`, kein Superuser, kein `BYPASSRLS`,
   `default_transaction_read_only = on`, `statement_timeout = 5s`, `SELECT` auf `products`, `product_fits`, `customers`,
   `contacts` und `activities` mit je einer `SELECT`-Policy. `discount_rules` ist für sie nicht lesbar (kommt mit F09).
2. Jede Anfrage öffnet ihre eigene Verbindung (kein Pool), schreibgeschützt, mit Zeitlimit. Die URL kommt nur aus
   `MCP_SERVER_DATABASE_URL`, mit denselben `sslmode`-Regeln wie das Seed-Skript.
3. Beim Start prüft `db.verify_read_only_role` die Rolle der Verbindung und bricht den Start ab, wenn sie mehr als lesen darf:
   kein Superuser, kein `BYPASSRLS`, kein `INSERT`, `UPDATE`, `DELETE` oder `TRUNCATE` (auch nicht als Spaltenrecht) auf den fünf
   Tabellen, `SELECT` auf allen fünf, kein `CREATE` im aktuellen Schema, alle fünf Tabellen vorhanden. Die Prüfung läuft nur mit
   lesenden Abfragen. Scheitert sie, endet der Start mit Exit-Code 1 und nennt auf stderr nur Kurzbezeichnungen
   (`rolle-ist-superuser`, `schreibrecht:<RECHT>:<tabelle>` und so weiter). **Es gibt keinen Schalter**, der sie abschaltet.
4. Fehlermeldungen und Logs nennen nie Eingabewerte, Rollenname, Host, Benutzer, Datenbankname, URL oder Passwort. Eine Ausnahme
   liegt im SDK: Fehlt ein Pflichtargument, gibt es den Argument-Dict (bis etwa 50 Zeichen) in der Fehlermeldung zurück. Das erreicht
   nur den Aufrufer und ist eine bekannte Einschränkung (Issue #21, Lösung offen). Eingaben prüft `limits.py`, bevor die
   Datenbank berührt wird; Werte gehen nur als Parameter an feste SQL-Texte.
5. Die Werkzeuge sind synchrone Funktionen (`def`), die Abfragen laufen mit synchronem psycopg, nicht mit asyncpg. Das SDK führt
   synchrone Werkzeuge laut Quelltext (mcp 2.3.0) in einem Worker-Thread aus, die Abfrage blockiert die Schleife dann nicht; per Test
   ist das nicht belegt.

## Alternativen
- Eine Rolle mit `BYPASSRLS` oder ein Superuser (die Annahme vor F07): verworfen. Sie kann schreiben und umgeht die Sperre,
  auf die sich der Auftrag verlässt.
- Schutz nur im Code oder im Prompt: verworfen. Auftrag 5 verlangt eine technische Durchsetzung.
- Eine Startprüfung mit einem echten Schreibversuch: verworfen. Die Prüfung soll selbst nichts verändern.
- Eine abschaltbare oder zurückgestellte Startprüfung: verworfen. Ein Schalter hebt die Sperre bei der ersten Fehlkonfiguration auf.

## Konsequenzen
- Der Mensch spielt das Skript ein und setzt das Passwort getrennt. Nach jedem `python -m db.seed` ist es erneut auszuführen,
  weil der Reset Rechte und Policies löscht.
- Neue Abhängigkeiten `psycopg[binary]` und `pydantic`, beide gepinnt. Nur `db.py` importiert den Treiber (Test per AST).
- Tests mit Datenbank brauchen `TEST_DATABASE_URL` (nur `localhost:5432`); zwei Testgruppen brauchen einen Superuser. Die CI
  startet dafür PostgreSQL 17.
- `sslmode=require` verschlüsselt, prüft aber das Serverzertifikat nicht und schützt daher nur vor passivem Mitlesen
  (`docs/DATENMODELL.md`, Abschnitt 4, sagt dasselbe zum Seed-Skript). Eine strengere Prüfung (`verify-full`) für Hosts außer
  localhost ist für das Deployment (F30) als Härtung vorgesehen.
- `hoffmann_data/dburl.py` ist eine gekürzte Kopie der URL-Regeln aus `db/seed/guard.py`; `tests/mcp_server/test_dburl_parity.py`
  hält beide gleich. Beides zusammenzuführen ist offen.

### Grenzen der Startprüfung
Die Prüfung ist eine **Momentaufnahme** beim Start: Rechte, die später vergeben werden, fallen erst beim nächsten Start auf.
Sie prüft **nur diese fünf Tabellen** und das Recht `CREATE` im aktuellen Schema. Nicht geprüft werden andere Tabellen,
Funktionen, Sequenzen und Schemas, Temporärtabellen und Rechte, die erst nach einem Wechsel in eine andere Rolle gälten. Sie
prüft auch nicht, ob die Policies die richtigen Zeilen freigeben. **Die Datenbank bleibt die eigentliche Sperre**: Die Prüfung
erkennt eine falsche URL oder Rolle, ersetzt aber die Rolle nicht. Unter PostgreSQL bis Version 14 hat PUBLIC `CREATE` auf dem
Schema `public`, dort kann `create-im-schema` ein Fehlalarm sein. Gegen Supabase ist die Prüfung noch nicht erprobt.

### Abweichungen und Lehren aus F07
- `pydantic` ist explizit gepinnt (kein `typing_extensions`). `query`, `limit`, `article_number` sind `Annotated[Any, WithJsonSchema]`
  mit eigener Prüfung: Das SDK gibt bei Pydantic-Fehlern den Eingabewert zurück, die einfache Signatur liefert keine feste Meldung.
- Der JSON-Vorparser des SDKs ersetzt Texte, die als `null`, Liste oder Objekt lesbar sind (nicht `true` und `false`); sie werden abgelehnt.
- Ein einzelnes Surrogat weist der Transport ab (HTTP 400). `limits.py` lehnt es trotzdem ab: Der Schutz darf nicht vom Transportpfad abhängen.
- Die Großschreibung der Artikelnummer entsteht in Python (nur ASCII), nicht mit `upper()` in SQL: Das hängt vom Locale der Datenbank ab:
  Das Zeichen U+017F (langes s) wird von `upper()` in der Datenbank zu S, aus U+017Fb-1001 wird SB-1001. Umlaute in Großschreibung brauchen ein UTF-8-Locale (CI-Image `postgres:17`, nicht `alpine`).
- Rechtenamen stehen nur als Parameter in der Abfrage, nie im SQL-Text. Die Paketregel (`test_package_sql_rules.py`) lässt in `db.py` genau die
  Zeichenketten `INSERT`, `UPDATE`, `DELETE`, `TRUNCATE`, `CREATE` und `create-im-schema` zu; sie hält jede Zeichenkette mit `select` für SQL,
  deshalb stehen die Kurzbezeichnungen `kein-select:<tabelle>` als feste Literale in `NO_SELECT_FAILURES`.
- Die Tests für Superuser und `BYPASSRLS` brauchen einen Superuser: lokal werden sie übersprungen, in GitHub Actions sind sie ein Fehler.
