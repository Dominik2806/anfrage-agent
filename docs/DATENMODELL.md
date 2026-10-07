# Datenmodell (F05)

Dieses Dokument beschreibt das Datenmodell der Stammdaten in PostgreSQL (Supabase). Es gehört zu Feature F05 (Synthetische Testdaten) und dient F07, F08, F16 und F23 als Grundlage. Bei Widerspruch gilt `docs/AUFTRAG.md`.

Tabellen für spätere Features (Anfragen, Entwürfe, Läufe) sind hier bewusst nicht enthalten.

## 1. Grundsätze

- Bezeichner (Tabellen, Spalten, Auswahlwerte) sind Englisch, Inhalte Deutsch.
- Auswahlwerte sind englische Kleinbuchstaben-Slugs, abgesichert durch CHECK-Constraints.
- Jede Tabelle hat einen Primärschlüssel `id bigint GENERATED ALWAYS AS IDENTITY`.
- Preise sind `numeric(10,2)`.
- Ausschließlich synthetische Daten. Domains und E-Mail-Adressen enden auf `.example`. Es gibt keine Telefonnummern.
- Row Level Security ist auf allen Tabellen aktiviert, ohne Policies. Der Datenservice greift über eine Serverrolle zu, die RLS umgeht. Über die Supabase-API (anon, authenticated) ist nichts lesbar.
- Alle Fremdschlüssel sind `ON DELETE RESTRICT` (ausdrücklich angegeben): Eine Zeile, auf die verwiesen wird, lässt sich nicht löschen.
- Fremdschlüssel-Spalten haben bewusst keine zusätzlichen Indizes, nur die Indizes der UNIQUE-Constraints. Bei ca. 40 Artikeln und ca. 20 Firmen lohnen sie nicht. Ein späteres Feature ergänzt sie bei Bedarf.
- Mindestversion PostgreSQL 15 (wegen `UNIQUE NULLS NOT DISTINCT`).
- Volltextsuche und `pg_trgm` kommen erst mit F07. Die Beschreibungen enthalten Alternativbezeichnungen, damit umschriebene Produkte auffindbar sind.

## 2. Tabellen

### 2.1 products

| Spalte | Typ | Constraints |
|---|---|---|
| id | bigint | PK |
| article_number | text | NOT NULL, UNIQUE, CHECK `~ '^[A-Z]{2}-[0-9]{4}(-[A-Z0-9]{1,4})?$'` (Varianten erlaubt) |
| name | text | NOT NULL |
| category | text | NOT NULL, CHECK IN (`conveyor`, `housing`, `spare_part`, `service_contract`) |
| description | text | NOT NULL, enthält Alternativbezeichnungen (z. B. „auch: Gurtförderer“) |
| technical_data | jsonb | NOT NULL DEFAULT `'{}'`, CHECK `jsonb_typeof(technical_data) = 'object'` |
| list_price | numeric(10,2) | NOT NULL, CHECK `>= 0` |
| price_unit | text | NOT NULL, CHECK IN (`piece`, `meter`, `year`) |
| lead_time_days | integer | NOT NULL, CHECK `>= 0` |
| is_active | boolean | NOT NULL DEFAULT true |

### 2.2 product_fits

Ersatzteil passt zu Anlage (n:m).

| Spalte | Typ | Constraints |
|---|---|---|
| id | bigint | PK |
| product_id | bigint | NOT NULL, FK → products(id) ON DELETE RESTRICT, das Teil |
| fits_product_id | bigint | NOT NULL, FK → products(id) ON DELETE RESTRICT, die Anlage |
| note | text | NULL |
| (Tabelle) | | UNIQUE (product_id, fits_product_id), CHECK `product_id <> fits_product_id` |

### 2.3 customers

| Spalte | Typ | Constraints |
|---|---|---|
| id | bigint | PK |
| company_name | text | NOT NULL; eindeutiger Index auf `lower(company_name)` |
| domain | text | NOT NULL, UNIQUE, CHECK `domain = lower(domain)` und `domain ~ '^[a-z0-9-]+(\.[a-z0-9-]+)*\.example$'` |
| industry | text | NOT NULL, CHECK IN (`automotive`, `food`, `packaging`, `metalworking`, `plastics`, `logistics`, `other`) |
| country | text | NOT NULL, CHECK IN (`de`, `at`, `ch`) |
| status | text | NOT NULL, CHECK IN (`existing`, `lead`, `inactive`) |
| created_at | timestamptz | NOT NULL DEFAULT now() |

Der Umlaut-Test zum eindeutigen Index auf `lower(company_name)` hängt von der Locale der Datenbank ab.

### 2.4 contacts

| Spalte | Typ | Constraints |
|---|---|---|
| id | bigint | PK |
| customer_id | bigint | NOT NULL, FK → customers(id) ON DELETE RESTRICT |
| first_name | text | NOT NULL |
| last_name | text | NOT NULL |
| email | text | NOT NULL, UNIQUE, CHECK `email = lower(email)` und `email ~ '^[^@\s]+@[a-z0-9-]+(\.[a-z0-9-]+)*\.example$'` |
| job_title | text | NULL |
| language | text | NOT NULL, CHECK IN (`de`, `en`) |
| created_at | timestamptz | NOT NULL DEFAULT now() |
| (Tabelle) | | UNIQUE (id, customer_id), für den zusammengesetzten FK der Aktivitäten |

### 2.5 activities

Historie (frühere Anfragen und Aufträge) und neue Protokolleinträge.

| Spalte | Typ | Constraints |
|---|---|---|
| id | bigint | PK |
| customer_id | bigint | NOT NULL, FK → customers(id) ON DELETE RESTRICT |
| contact_id | bigint | NULL, zusammengesetzter FK (contact_id, customer_id) → contacts(id, customer_id) ON DELETE RESTRICT |
| product_id | bigint | NULL, FK → products(id) ON DELETE RESTRICT |
| type | text | NOT NULL, CHECK IN (`inquiry`, `quote`, `order`, `complaint`, `service`, `note`) |
| occurred_at | timestamptz | NOT NULL |
| subject | text | NOT NULL |
| summary | text | NOT NULL |
| amount_eur | numeric(10,2) | NULL, CHECK `>= 0` |
| created_by | text | NOT NULL, CHECK IN (`seed`, `agent`, `staff`) |
| (Tabelle) | | CHECK `amount_eur IS NULL OR type IN ('quote', 'order')` |

Ein Bezug auf Anfragen, Entwürfe oder Läufe fehlt bewusst. Ihn ergänzt das Feature, das diese Tabellen einführt (F19).

### 2.6 discount_rules

| Spalte | Typ | Constraints |
|---|---|---|
| id | bigint | PK |
| customer_status | text | NULL (= alle), CHECK IN (`existing`, `lead`) |
| product_category | text | NULL (= alle), CHECK IN (`conveyor`, `housing`, `spare_part`, `service_contract`) |
| min_quantity | integer | NOT NULL DEFAULT 1, CHECK `>= 1` |
| max_discount_percent | numeric(5,2) | NOT NULL, CHECK `BETWEEN 0 AND 100` |
| description | text | NOT NULL, Regeltext auf Deutsch |
| (Tabelle) | | UNIQUE NULLS NOT DISTINCT (customer_status, product_category, min_quantity) |

`max_discount_percent` ist die Eskalationsschwelle und keine Zusage. Der Agent sagt nie Rabatt zu. Ein Wunsch oberhalb der Schwelle wird eskaliert (Auftrag 4.8).

## 3. Nutzung durch die Features

| Tabelle | F05 | F07 Lese-Tools | F08 Schreib-Tools | F16 Eskalationsregeln | F23 Bewertungsset |
|---|---|---|---|---|---|
| products | ca. 40 Artikel | `search_products`, `get_product` (Preis, Einheit, Lieferzeit) | – | Kein Treffer bedeutet unbekanntes Produkt | Erwartete Artikelnummern; „Unbekannt“ nutzt Artikel, die fehlen |
| product_fits | Zuordnung Teil ↔ Anlage | `get_product` liefert „passt zu“ | – | – | Fälle „Ersatzteil für Anlage X“ |
| customers | ca. 20 Firmen | `find_customer` über Firma oder Domain | `create_lead` (Status `lead`); Domain und Firmenname verhindern Dubletten | Unbekannter Absender wird als Lead behandelt | Bestandskunde, Neukunde, gefälschter Absender |
| contacts | Ansprechpartner je Firma | `find_customer` liefert Kontakte | `create_lead` legt den Kontakt mit an | – | Absender-Abgleich |
| activities | Frühere Anfragen und Aufträge | `find_customer` liefert die Historie (mit Betrag und Artikel) | `log_activity` | Frühere Reklamationen als Kontext | Historie als Fallgrundlage |
| discount_rules | Rabattregeln; Quelle für `rabatte.md` | Quelle für `policy://rabatte` (F09) | – | Schwelle „Rabatt über Regel“ | Fälle „Rabattforderung“ |

## 4. Befüllbefehl

| Aspekt | Festlegung |
|---|---|
| Aufruf | Ein einziger Befehl erzeugt die Tabellen, befüllt die Datenbank und schreibt `data/richtlinien/rabatte.md` (F05). |
| Umsetzung | Python mit psycopg 3. Die Abhängigkeit ist freigegeben; Versionen und Dateiort werden später festgelegt. |
| Wiederholbar | Mehrfaches Ausführen führt zum selben fachlichen Stand. |
| Natürliche Schlüssel | Das Skript verweist über natürliche Schlüssel (`article_number`, `domain`, `email`), nie über feste IDs. Bewertungsfälle (F23) verweisen ebenfalls darauf. |
| Löschschutz | Bevor vorhandene Daten gelöscht werden, muss die Umgebungsvariable `SEED_CONFIRM_RESET` gesetzt sein. Ihr Wert muss der Hostname der Zieldatenbank sein (nicht `yes`). Das Skript gibt den Host vor dem Löschen aus und bricht bei Abweichung ab, ohne etwas zu löschen. |
| Atomar | Löschen und Befüllen laufen in einer Transaktion. Bei einem Fehler bleibt der alte Stand erhalten. |
| Zugangsdaten | Die Datenbank-URL kommt nur aus einer Umgebungsvariable, nie aus dem Repository. `.env.example` enthält nur leere Beispielwerte. |
| Richtlinien | Quelle der Rabattregeln ist die Tabelle `discount_rules`. Das Skript erzeugt daraus `data/richtlinien/rabatte.md` und kennzeichnet die Datei als generiert. Tonalitätsleitfaden, Eskalationsregeln und Signatur liegen als Dateien in `data/richtlinien/`. |
| Reihenfolge | Zuerst laufen die Konsistenzprüfungen (Abschnitt 5), dann wird geschrieben. Jede Verletzung bricht den Lauf ab. |

## 5. Konsistenzprüfungen

Diese Prüfungen kann kein CHECK leisten. Sie laufen im Seed-Skript und sind per Test abgesichert. Die Prüfungen 2 und 3 gelten später auch für die Schreib-Tools aus F08.

| # | Prüfung | Warum kein CHECK |
|---|---|---|
| 1 | In `product_fits` hat `product_id` die Kategorie `spare_part` und `fits_product_id` die Kategorie `conveyor` oder `housing`. | Betrifft Zeilen in einer anderen Tabelle. |
| 2 | Die Domain der E-Mail eines Kontakts entspricht `customers.domain` seines Kunden. | Vergleich über zwei Tabellen. |
| 3 | `activities.occurred_at` liegt nicht vor `customers.created_at`. Im Seed wird `created_at` der Kunden explizit gesetzt, nicht per `now()`. | Vergleich über zwei Tabellen. |
| 4 | Alle Artikelbezüge existieren (FK auf `products`). | Der FK sichert die Existenz in `activities` und `product_fits`; Verweise in Texten (Prüfung 5) kennt er nicht. |
| 5 | Artikelnummern, die in Freitexten der Testdaten vorkommen, existieren im Katalog. | Verweise in Freitext sind für die Datenbank unsichtbar. |

Dass der Kontakt einer Aktivität zum Kunden der Aktivität gehört, sichert der zusammengesetzte FK (`contact_id`, `customer_id`) ab.

## 6. Tests

Die Tests der Constraints laufen in der CI gegen einen Postgres-Container, der als Dienst im Job `Schema-Tests` des Workflows bereitgestellt wird. Den Workflow ändert nur der Mensch.

Regeln der Tests (`tests/db/`):

- Die Verbindung kommt nur aus `TEST_DATABASE_URL`, nie aus `DATABASE_URL`. Der Host darf nur `localhost`, `127.0.0.1` oder `::1` sein, sonst bricht der Lauf mit einer klaren Meldung ab. Erlaubt ist nur Port 5432 (oder keine Angabe); mehrere Hosts, die Parameter `host`, `hostaddr` und `service` in der URL sowie die Umgebungsvariablen `PGHOSTADDR` und `PGSERVICE` werden abgelehnt, weil sie den geprüften Host überstimmen würden. Die Tests legen ein eigenes Schema `test_<zufall>` an und löschen es am Ende, nie in `public`.
- Fehlt `TEST_DATABASE_URL`, werden lokal nur die Tests mit Datenbank übersprungen. In GitHub Actions bricht der Lauf mit einem Fehler ab, damit die Pipeline nie grün ist, ohne dass die Tests liefen.
- Der RLS-Test legt eine Rolle an und braucht lokal das Recht `CREATEROLE` (in der CI ist `postgres` Superuser).
- Jeder Test läuft in einer Transaktion, die zurückgerollt wird. Negative Tests prüfen Fehlerklasse und Constraint-Namen (bei NOT NULL die Spalte).
