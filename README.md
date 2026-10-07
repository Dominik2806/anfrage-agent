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
2. `python -m db.seed` ausführen (unter Windows `.\.venv\Scripts\python.exe -m db.seed`). Das legt die Tabellen an und befüllt sie.
3. Sind die Tabellen schon vorhanden, setzt man zusätzlich `SEED_CONFIRM_RESET` auf genau den Host der Datenbank.
   Achtung: Der Reset löscht auch Zeilen, die Agent und Mitarbeitende später angelegt haben.

`python -m db.seed --nur-rabatte` erzeugt nur `data/richtlinien/rabatte.md` und braucht keine Datenbank.
Einzelheiten: [Datenmodell, Abschnitt 4](docs/DATENMODELL.md).

## Dokumente
- [Projektauftrag](docs/AUFTRAG.md)
- [Entwicklungsprotokoll](AGENT_LOG.md)
- [Änderungen](CHANGELOG.md)

## Lizenz
MIT, siehe [LICENSE](LICENSE).