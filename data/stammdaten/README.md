# Stammdaten (synthetisch)

Alle Daten sind synthetisch und fiktiv (Domains und Adressen enden auf `.example`, keine Telefonnummern). Das Seed-Skript (F05) liest diese Dateien und befüllt die Datenbank. Struktur: `db/schema.sql` und `docs/DATENMODELL.md`.

Stand: Schritte 2a und 2b (Katalog, Kunden, Kontakte). Aktivitäten und Rabattregeln folgen in 2c und 2d.

## Dateien

| Datei | Inhalt | Tabelle |
|---|---|---|
| `products.json` | 40 Artikel | `products` |
| `product_fits.json` | Zuordnung Ersatzteil zu Anlage | `product_fits` |
| `customers.json` | 21 Firmen (14 Bestandskunden, 6 Leads, 1 inaktiv) | `customers` |
| `contacts.json` | 35 Ansprechpersonen, 1 bis 2 je Firma | `contacts` |

## Natürliche Schlüssel

Verweise laufen nie über IDs, sondern über `article_number` (später auch `domain` und `email`). Das Skript löst sie beim Schreiben in IDs auf.

- `products.json`: Felder wie in der Tabelle `products` (ohne `id`). `technical_data` hat englische Schlüssel mit Einheit im Namen (`belt_width_mm`, `motor_power_kw`).
- `product_fits.json`: `part` (Artikelnummer des Ersatzteils), `fits` (Artikelnummer der Anlage oder des Gehäuses), `note` (optional).
- `customers.json`: Felder wie in der Tabelle `customers` (ohne `id`). Natürliche Schlüssel sind `domain` und `company_name` (ohne Beachtung der Groß-/Kleinschreibung). `created_at` steht absolut mit Zeitzone in den Daten.
- `contacts.json`: Felder wie in der Tabelle `contacts` (ohne `id`), statt `customer_id` das Feld `customer_domain`. Natürlicher Schlüssel ist `email`. Die Domain der E-Mail-Adresse ist die Domain der Firma (Konsistenzprüfung 2). `created_at` ist nie früher als `created_at` der Firma.
- Die Aktivitäten (2c) verweisen über `customer_domain` und optional `contact_email`. Sie liegen zeitlich nach `created_at` der Firma (Prüfung 3). Aktivitäten beginnen etwa im Januar 2025.

## Preise

- **`list_price` ist ein Netto-Listenpreis** (ohne Mehrwertsteuer) in Euro. `tonalitaet.md` verlangt deshalb „zuzüglich Mehrwertsteuer“.
- `price_unit`: `piece` (je Stück oder Einsatz), `meter` (je laufendem Meter), `year` (je Anlage und Jahr).
- Alle Zahlen sind Platzhalter für die Entwicklung und von der Fachseite nicht geprüft.

## Nummernkreise

| Präfix | Kategorie |
|---|---|
| `FB-1xxx` | `conveyor` (Förderbänder) |
| `SG-2xxx` | `housing` (Schutzgehäuse) |
| `ET-3xxx` | `spare_part` (Ersatzteile) |
| `WV-4xxx` | `service_contract` (Wartung) |

Ein Suffix (`-B8`, `-X2`, `-H1`, `-L`, `-X1`) kennzeichnet eine Variante.

## Absichtliche Lücken (für Bewertungsfälle, F23)

Diese Lücken sind gewollt. Sie dürfen nicht „repariert“ werden, weil Bewertungsfälle darauf aufbauen.

- **Teil ohne Zuordnung:** `ET-3009` (Lagersatz) hat keine Zeile in `product_fits`. Eine Anfrage „Lagersatz für Anlage X“ hat keine belegte Zuordnung und wird eskaliert.
- **Anlagen ohne oder mit wenigen Ersatzteilen:** `FB-1008`, `FB-1008-H1` und alle Gehäuse außer `SG-2004` haben kein zugeordnetes Ersatzteil; `FB-1006` nur `ET-3013`.
- **Varianten:** `FB-1001` hat die Breite 500 mm, `FB-1001-B8` die Breite 800 mm. Nennt ein Kunde nur `FB-1001`, aber 800 mm, ist das ein Widerspruch. Ersatzteile sind nur den Basisartikeln zugeordnet; Varianten erben die Zuordnung nicht automatisch.
- **Inaktiver Artikel:** `ET-3014` (`is_active` = false, ausgelaufen). Der Nachfolger `ET-3010` steht nur im Beschreibungstext und in `technical_data` (`successor`), nicht als Verweis auf einen Artikel in `product_fits`.
- **Ähnliche Namen:** Antriebsmotor 0,75 kW und 1,5 kW, Gurtförderer Leicht, Standard und Schwerlast, Wartungsvertrag Basis, Plus und Premium. Unscharfe Anfragen können mehrdeutig sein.
- **Alternativbezeichnungen:** Jede Beschreibung enthält Umschreibungen („Auch: …“), damit Produkte auch ohne Artikelnummer auffindbar sind.
- **Unbekannte Produkte (fehlen absichtlich, ohne Artikelnummer):** Rollenbahn, Kettenförderer, Hubtisch, Zahnriemenförderer, Sortieranlage, Kühlschmierstoff, Schulung und Software. Diese Begriffe dürfen in den Testdaten nicht mit einer Artikelnummer vorkommen.
- **Ähnliche Firmen (Absender-Abgleich):** „Hartmann Metallverarbeitung GmbH“ (`hartmann-metallverarbeitung.example`, Bestandskunde, Deutschland) und „Hartmann Metallbearbeitung GmbH“ (`hartmann-metallbearbeitung.example`, Lead, Österreich) sind verschiedene Firmen mit ähnlichem Namen und ähnlicher Domain. Beide Geschäftsführer heißen Hartmann (Bernd und Markus). Ein Absender der einen darf nicht dem anderen zugeordnet werden.
- **Inaktiver Kunde:** „Wiesental Metallguss GmbH“ (`inactive`, 1 Kontakt). Eine Anfrage von dort ist kein Neukunde, aber auch kein aktiver Bestandskunde.
- **Leads:** 6 Firmen mit Status `lead`, je ein Kontakt, angelegt 2025 und 2026. Sie haben keine Historie vor ihrem Anlagedatum.
- **Kunde für eine frühere Reklamation:** „Brenner Automotive Systeme GmbH“ (Bestandskunde seit 2019, zwei Kontakte, einer davon englischsprachig) ist als Kunde mit früherer Reklamation in den Aktivitäten (2c) vorgesehen.
- **Sprache:** 7 Kontakte mit `language` = `en`, darunter Kontakte in Deutschland und der Schweiz. Die Antwortsprache folgt der Anfrage, nicht dem Land.
- **Artikelnummern in Freitexten** müssen im Katalog existieren (Konsistenzprüfung 5, `docs/DATENMODELL.md` Abschnitt 5).

## Offene Bestätigung durch F16

`data/richtlinien/eskalation.md` enthält zwei Kriterien, die nicht aus dem Auftrag stammen: „bei Frist hoch“ und „Frist innerhalb weniger Tage“. F16 (Eskalationsregeln) soll sie bestätigen oder anpassen.
