# Stammdaten (synthetisch)

Alle Daten sind synthetisch und fiktiv (Domains und Adressen enden auf `.example`, keine Telefonnummern). Das Seed-Skript (F05) liest diese Dateien und befüllt die Datenbank. Struktur: `db/schema.sql` und `docs/DATENMODELL.md`.

Stand: Schritt 2d (Katalog, Kunden, Kontakte, Aktivitäten, Rabattregeln). Die Stammdaten sind vollständig.

## Dateien

| Datei | Inhalt | Tabelle |
|---|---|---|
| `products.json` | 40 Artikel | `products` |
| `product_fits.json` | Zuordnung Ersatzteil zu Anlage | `product_fits` |
| `customers.json` | 21 Firmen (14 Bestandskunden, 6 Leads, 1 inaktiv) | `customers` |
| `contacts.json` | 35 Ansprechpersonen, 1 bis 2 je Firma | `contacts` |
| `activities.json` | 77 frühere Anfragen, Angebote, Aufträge, Reklamationen, Serviceeinsätze und Notizen, chronologisch | `activities` |
| `discount_rules.json` | 18 Rabattregeln (interne Obergrenzen) | `discount_rules` |

## Natürliche Schlüssel

Verweise laufen nie über IDs, sondern über `article_number` (später auch `domain` und `email`). Das Skript löst sie beim Schreiben in IDs auf.

- `products.json`: Felder wie in der Tabelle `products` (ohne `id`). `technical_data` hat englische Schlüssel mit Einheit im Namen (`belt_width_mm`, `motor_power_kw`).
- `product_fits.json`: `part` (Artikelnummer des Ersatzteils), `fits` (Artikelnummer der Anlage oder des Gehäuses), `note` (optional).
- `customers.json`: Felder wie in der Tabelle `customers` (ohne `id`). Natürliche Schlüssel sind `domain` und `company_name` (ohne Beachtung der Groß-/Kleinschreibung). `created_at` steht absolut mit Zeitzone in den Daten.
- `contacts.json`: Felder wie in der Tabelle `contacts` (ohne `id`), statt `customer_id` das Feld `customer_domain`. Natürlicher Schlüssel ist `email`. Die Domain der E-Mail-Adresse ist die Domain der Firma (Konsistenzprüfung 2). `created_at` ist nie früher als `created_at` der Firma.
- `activities.json`: Felder wie in der Tabelle `activities` (ohne `id`). Verweise: `customer_domain` (Pflicht), `contact_email` und `article_number` (beide optional, `null` bei keinem Bezug). Pflichtfelder: `type`, `occurred_at`, `subject`, `summary`, `amount_eur` (explizit `null`, wenn kein Betrag) und `created_by` (immer `seed`).

Regeln der Aktivitäten (77 Einträge; Beträge, Sortierung, `.example` und Telefonnummern prüft `tests/seed/test_seed_data.py`):

- Die Einträge sind chronologisch nach `occurred_at` sortiert, mit Zeitzone. Der letzte Eintrag liegt am 22.09.2026, nicht nach dem 05.10.2026.
- `occurred_at` liegt nie vor `created_at` des Kunden (Konsistenzprüfung 3). Der früheste Eintrag eines aktiven Kunden stammt vom 16.01.2025; nur der inaktive Kunde Wiesental hat ältere Einträge (ab 2017).
- Die E-Mail-Domain des Kontakts entspricht `customer_domain` (Konsistenzprüfung 2).
- `amount_eur` ist nur bei `quote` und `order` gesetzt, netto, und immer Listenpreis mal ganzzahlige Menge, ohne Abschläge. Die Menge steht in der Zusammenfassung (bei Wartungsverträgen in Jahren).
- Die Texte (`subject`, `summary`) enthalten keine Artikelnummern. Der Artikelbezug steht nur in `article_number` und verweist auf Artikel im Katalog (Konsistenzprüfung 5).

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

## Rabattregeln

`discount_rules.json`: Felder wie in der Tabelle `discount_rules` (ohne `id`): `customer_status` (`existing`, `lead` oder `null` für alle), `product_category` (eine der vier Kategorien oder `null` für alle), `min_quantity`, `max_discount_percent` und `description` (Regeltext auf Deutsch).

- Es gibt keinen natürlichen Schlüssel. Die Eindeutigkeit läuft über die Kombination aus `customer_status`, `product_category` und `min_quantity`. Keine zwei Regeln haben denselben Geltungsbereich.
- `max_discount_percent` ist eine interne Obergrenze als Maßstab für den Innendienst, keine Zusage. Der Agent sagt nie Rabatte zu und nennt sie nie in einem Entwurf (Auftrag 5 und 5.1).
- Die Mindestmenge richtet sich nach der Preiseinheit des Artikels (Meter, Stück oder Jahr, siehe `products.json`). Die Beschreibung nennt die Einheit.
- Die Daten enthalten nur Stufen. Welche Regel für einen Fall gilt (speziellste Regel, höchste passende Mindestmenge), legt F16 fest.
- Für Kunden mit Status `inactive` gibt es keine eigene Regel. Dort greift nur die allgemeine Obergrenze (ohne Geltungsbereich). Details legt F16 fest.
- Wartungsverträge haben für Bestandskunden und Leads eine eigene Regel mit 0 Prozent.
- `data/richtlinien/rabatte.md` wird aus dieser Tabelle vom Seed-Skript generiert und darf nicht von Hand geändert werden.
- Alle Werte sind fiktive Platzhalter. Die Fachseite hat sie nicht geprüft.

## Absichtliche Lücken (für Bewertungsfälle, F23)

Diese Lücken sind gewollt. Sie dürfen nicht „repariert“ werden, weil Bewertungsfälle darauf aufbauen.

- **Teil ohne Zuordnung:** `ET-3009` (Lagersatz) hat keine Zeile in `product_fits`. Eine Anfrage „Lagersatz für Anlage X“ hat keine belegte Zuordnung und wird eskaliert.
- **Anlagen ohne oder mit wenigen Ersatzteilen:** `FB-1008`, `FB-1008-H1` und alle Gehäuse außer `SG-2004` haben kein zugeordnetes Ersatzteil; `FB-1006` nur `ET-3013`.
- **Varianten:** `FB-1001` hat die Breite 500 mm, `FB-1001-B8` die Breite 800 mm. Nennt ein Kunde nur `FB-1001`, aber 800 mm, ist das ein Widerspruch. Ersatzteile sind nur den Basisartikeln zugeordnet; Varianten erben die Zuordnung nicht automatisch.
- **Inaktiver Artikel:** `ET-3014` (`is_active` = false, ausgelaufen). Der Nachfolger `ET-3010` steht im Beschreibungstext, in `technical_data` (`successor`) und als Notiz („Ersatz für die ausgelaufene Steuerung“) bei der Zuordnung von `ET-3010` zu `FB-1007` in `product_fits.json`.
- **Ähnliche Namen:** Antriebsmotor 0,75 kW und 1,5 kW, Gurtförderer Leicht, Standard und Schwerlast, Wartungsvertrag Basis, Plus und Premium. Unscharfe Anfragen können mehrdeutig sein.
- **Alternativbezeichnungen:** Jede Beschreibung enthält Umschreibungen („Auch: …“), damit Produkte auch ohne Artikelnummer auffindbar sind.
- **Unbekannte Produkte (fehlen absichtlich, ohne Artikelnummer):** Rollenbahn, Kettenförderer, Hubtisch, Zahnriemenförderer, Sortieranlage, Kühlschmierstoff, Schulung und Software. Diese Begriffe dürfen in den Testdaten nicht mit einer Artikelnummer vorkommen.
- **Ähnliche Firmen (Absender-Abgleich):** „Hartmann Metallverarbeitung GmbH“ (`hartmann-metallverarbeitung.example`, Bestandskunde, Deutschland) und „Hartmann Metallbearbeitung GmbH“ (`hartmann-metallbearbeitung.example`, Lead, Österreich) sind verschiedene Firmen mit ähnlichem Namen und ähnlicher Domain. Beide Geschäftsführer heißen Hartmann (Bernd und Markus). Ein Absender der einen darf nicht dem anderen zugeordnet werden.
- **Inaktiver Kunde:** „Wiesental Metallguss GmbH“ (`inactive`, 1 Kontakt). Eine Anfrage von dort ist kein Neukunde, aber auch kein aktiver Bestandskunde.
- **Leads:** 6 Firmen mit Status `lead`, je ein Kontakt, angelegt 2025 und 2026. Sie haben keine Historie vor ihrem Anlagedatum.
- **Reklamation, erledigt:** Brenner Automotive Systeme GmbH, 18.02.2025, Fördergurt (`ET-3007`), Kontakt Jonas Feldmann (Englisch). Abschlussnotiz am 06.03.2025: Ursache zu hohe Gurtspannung, Gurt ersetzt, Kunde bestätigt die Erledigung.
- **Reklamation mit verärgerter Folgeanfrage:** Kunststofftechnik Vogel GmbH, Reklamation am 10.06.2025 (verspätete Lieferung Tragrollen, `ET-3006`), abgeschlossen am 24.06.2025 (Kundin bestätigt, äußert aber weiter Unmut). Folgeanfrage am 14.04.2026 (`FB-1006`) mit Bezug auf die Reklamation, Ton deutlich verärgert; Angebot am 21.04.2026.
- **Reklamation, offen:** Rheinpack Verpackungen GmbH, 22.09.2026, laute Laufgeräusche an einem Getriebe (`ET-3003`, bestellt am 14.07.2026). Die Rückmeldung der Technik steht aus; danach gibt es keinen weiteren Eintrag.
- **Nachfrage zu früherer Anfrage:** Alpenmilch Verarbeitung AG, 03.06.2025 (Kontakt Sabrina Hofer). Sie fragt nach dem Stand der Anfrage vom 08.04.2025 und dem Angebot vom 15.04.2025 (`FB-1008`). Der Auftrag folgt am 24.06.2025.
- **Ähnlich benannte Firmen:** Hartmann Metallverarbeitung GmbH (Bestandskunde): Bestellung 27.02.2025, Anfrage 05.08.2025, Angebot 19.08.2025, Abgrenzungsnotiz 30.09.2025, Auftrag 02.06.2026. Hartmann Metallbearbeitung GmbH (Lead): Erstanfrage und Notiz „Abgrenzung zu Hartmann Metallverarbeitung“, beide am 07.04.2025. Die Notizen halten nur fest, dass es zwei verschiedene Firmen sind. Die Zuordnung eines Absenders erfolgt über die Domain.
- **Unbekanntes Produkt:** Kroll Maschinenvertrieb GmbH (Lead), 16.03.2026, Anfrage nach einem Kettenförderer (`article_number` ist `null`), Notiz am 19.03.2026.
- **Ersatzteilanfrage ohne belegte Zuordnung:** Tiroler Backwaren GmbH, 27.01.2026, Gurtabstreifer (`ET-3011`) für den abspritzbaren Edelstahl-Gurtförderer. Der Eintrag nennt keine Artikelnummer der Anlage. In `product_fits.json` passt `ET-3011` nur zu `FB-1004`; Tiroler hat `FB-1008-H1` am 14.05.2025 bestellt.
- **Angebote ohne Auftrag:** Bergmann Fahrzeugteile GmbH, 05.03.2025 (`FB-1005`, Eintrag: „Bisher keine Rückmeldung des Kunden“); Zeller Spritzguss GmbH, 10.02.2026 (`ET-3016`); Brenner Automotive Systeme GmbH, 24.02.2026 (`FB-1001-B8`); Kunststofftechnik Vogel GmbH, 21.04.2026 (`FB-1006`).
- **Inaktiver Kunde in der Historie:** Wiesental Metallguss GmbH, Aufträge am 22.05.2017 (`FB-1007`) und 10.09.2018, Notiz „Kunde inaktiv gesetzt“ am 05.11.2019, danach keine Einträge.
- **Inaktiver Artikel in der Historie:** Wiesental Metallguss GmbH bestellte am 10.09.2018 den inzwischen ausgelaufenen Artikel `ET-3014`.
- **Wartungsverträge:** Aufträge: Zeller 16.01.2025 (`WV-4001`), Alpenmilch 22.01.2025 (`WV-4002`), Helvetia Pack 23.01.2025 (`WV-4005`), Swissform 28.01.2025 (`WV-4003`), Brenner 30.01.2025 (`WV-4002`, Angebot vom 20.01.2025), Frischkost Wagner 30.01.2025 (`WV-4001`), Rheinpack 19.05.2025 (`WV-4001`). Serviceeinsätze: Nordwind 20.05.2026 und Donau 18.08.2026 (je `WV-4004`), Swissform 16.06.2026 (`WV-4003`).
- **Kunde ohne Aktivitäten:** Diesen Fall gibt es nicht. Jede der 21 Firmen hat mindestens einen Eintrag (Leads 1 bis 2).
- **Englische Einträge:** Aktivitäten mit englischen Kontakten sind auf Englisch geschrieben.
- **Sprache:** 7 Kontakte mit `language` = `en`, darunter Kontakte in Deutschland und der Schweiz. Die Antwortsprache folgt der Anfrage, nicht dem Land.
- **Artikelnummern in Freitexten** müssen im Katalog existieren (Konsistenzprüfung 5, `docs/DATENMODELL.md` Abschnitt 5).

## Offene Bestätigung durch F16

`data/richtlinien/eskalation.md` enthält zwei Kriterien, die nicht aus dem Auftrag stammen: „bei Frist hoch“ und „Frist innerhalb weniger Tage“. F16 (Eskalationsregeln) soll sie bestätigen oder anpassen.
