---
name: adr-schreiben
description: "Verwende diesen Skill, wenn eine wesentliche Architekturentscheidung getroffen oder nachträglich dokumentiert werden soll (z. B. Wahl von Datenbank, Modell, Transport, Leitplanken-Ansatz) oder wenn der Nutzer eine ADR oder Entscheidungsbegründung verlangt. Schreibt eine kurze Begründung nach docs/adr/."
---

# ADR schreiben

Eine ADR (Architekturentscheidung) begründet eine wesentliche Entscheidung kurz und nachvollziehbar
(Auftrag Kapitel 10.1, `docs/adr/`). Pro Datei genau eine Entscheidung.

## Schritte
1. Kläre mit dem Nutzer, welche Entscheidung dokumentiert wird. Fehlen Kontext, Alternativen oder Gründe, frage nach.
   Erfinde keine Gründe oder Alternativen; stütze dich nur auf `docs/AUFTRAG.md`, den Code und das Gespräch.
2. Liste `docs/adr/` auf und bestimme die nächste freie vierstellige Nummer (`0001`, `0002`, …). Die `README.md` zählt nicht.
3. Lege `docs/adr/NNNN-kurztitel-in-kebab-case.md` an. Schreibe nur in `docs/adr/`.
4. Verwende diese Vorlage:

```markdown
# ADR NNNN: Titel der Entscheidung

- Status: Vorgeschlagen | Angenommen | Ersetzt durch ADR NNNN
- Datum: JJJJ-MM-TT

## Kontext
Welches Problem oder welche Anforderung (mit Verweis auf Feature oder Auftragskapitel) macht die Entscheidung nötig?

## Entscheidung
Was wird getan? Ein bis drei Sätze.

## Alternativen
Welche Optionen wurden erwogen, und warum wurden sie verworfen?

## Konsequenzen
Was wird dadurch einfacher, was schwieriger? Welche Pflichten oder Risiken entstehen?
```

5. Halte die Datei kurz (etwa eine Seite). Schreibe auf Deutsch.
6. Ersetzt die Entscheidung eine frühere ADR, setze deren Status auf „Ersetzt durch ADR NNNN“; ändere sonst keine bestehenden ADRs.
7. Fasse zusammen, welche Datei entstanden ist, und weise darauf hin, dass sie im selben Branch und Pull Request wie die Umsetzung committet wird.
