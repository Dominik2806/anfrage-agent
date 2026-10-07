<!-- GENERIERT aus data/stammdaten/discount_rules.json durch python -m db.seed. Nicht von Hand ändern. -->

# Rabattregeln (intern)

Diese Werte sind interne Obergrenzen und ein Maßstab für den Innendienst. Sie sind keine Zusage. Der Agent nennt sie nie in einem Antwortentwurf und sagt nie einen Rabatt zu, auch nicht innerhalb der Regel (siehe eskalation.md und tonalitaet.md).

Alle Werte sind fiktive Platzhalter und von der Fachseite nicht geprüft.

| Kundenstatus | Kategorie | ab Menge | Obergrenze (Prozent) | Beschreibung |
|---|---|---|---|---|
| alle | alle | 1 | 1,00 | Allgemeine Obergrenze ohne Geltungsbereich, alle Kunden und Kategorien, ab Menge 1 |
| existing | conveyor | 1 | 3,00 | Bestandskunde, Förderbänder ab 1 Meter bzw. Stück |
| existing | conveyor | 10 | 5,00 | Bestandskunde, Förderbänder ab 10 Meter bzw. Stück |
| existing | conveyor | 25 | 8,00 | Bestandskunde, Förderbänder ab 25 Meter bzw. Stück |
| existing | housing | 1 | 2,00 | Bestandskunde, Schutzgehäuse ab 1 Stück bzw. Meter |
| existing | housing | 5 | 4,00 | Bestandskunde, Schutzgehäuse ab 5 Stück bzw. Meter |
| existing | housing | 20 | 6,00 | Bestandskunde, Schutzgehäuse ab 20 Stück bzw. Meter |
| existing | service_contract | 1 | 0,00 | Bestandskunde, Wartungsverträge ab 1 Jahr, kein Spielraum |
| existing | spare_part | 1 | 2,00 | Bestandskunde, Ersatzteile ab 1 Stück bzw. Meter |
| existing | spare_part | 10 | 3,00 | Bestandskunde, Ersatzteile ab 10 Stück bzw. Meter |
| existing | spare_part | 50 | 5,00 | Bestandskunde, Ersatzteile ab 50 Stück bzw. Meter |
| lead | conveyor | 1 | 1,00 | Lead, Förderbänder ab 1 Meter bzw. Stück |
| lead | conveyor | 25 | 2,50 | Lead, Förderbänder ab 25 Meter bzw. Stück |
| lead | housing | 1 | 0,50 | Lead, Schutzgehäuse ab 1 Stück bzw. Meter |
| lead | housing | 20 | 1,50 | Lead, Schutzgehäuse ab 20 Stück bzw. Meter |
| lead | service_contract | 1 | 0,00 | Lead, Wartungsverträge ab 1 Jahr, kein Spielraum |
| lead | spare_part | 1 | 0,00 | Lead, Ersatzteile ab 1 Stück bzw. Meter, kein Spielraum |
| lead | spare_part | 50 | 1,00 | Lead, Ersatzteile ab 50 Stück bzw. Meter |
