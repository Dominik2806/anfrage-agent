---
name: eval-fall-anlegen
description: "Verwende diesen Skill, wenn ein neuer Bewertungsfall angelegt werden soll: wenn ein Fehler gefunden wurde (er wird vor der Behebung als Fall aufgenommen), bei einem abgelehnten oder stark bearbeiteten Entwurf aus den Rückmeldungen oder beim Aufbau des Bewertungssets (mindestens 50 Fälle, Auftrag Kapitel 8.2). Schreibt eine Fall-Datei nach evals/faelle/."
---

# Bewertungsfall anlegen

Jeder gefundene Fehler wird zuerst als Bewertungsfall aufgenommen, dann behoben (Auftrag Kapitel 8.3).
Format: **eine JSON-Datei je Fall** unter `evals/faelle/`. Schreibe nur nach `evals/`; lege `evals/faelle/` an, falls es fehlt.

## Schritte
1. Kläre mit dem Nutzer: Gruppe, Herkunft und erwartetes Ergebnis. Fehlen Angaben, frage nach, statt zu raten.
2. Gruppe wählen (Auftrag Kapitel 8.2): `standard`, `unscharf`, `sprache`, `heikel`, `manipulation`, `unbekannt`.
3. Liste `evals/faelle/` auf und bestimme die nächste laufende Nummer `nnn` (dreistellig) innerhalb der Gruppe.
4. Lege `evals/faelle/<gruppe>-<nnn>-<kurztitel-in-kebab-case>.json` an.
5. Prüfe, dass die Datei gültiges JSON ist, und fasse den Fall kurz zusammen.

## Format

```json
{
  "id": "heikel-001",
  "gruppe": "heikel",
  "titel": "Verärgerte Reklamation mit Produktionsstillstand",
  "herkunft": "neu",
  "anfrage": {
    "absender": "name@example.com",
    "betreff": "Betreff der Anfrage",
    "text": "Text der Anfrage"
  },
  "erwartet": {
    "kategorie": "Reklamation",
    "dringlichkeit": "hoch",
    "eskalation": true,
    "eskalationsgrund": "Reklamation",
    "pflicht_artikel": [],
    "verboten": ["verbindlicher Liefertermin", "Rabattzusage"]
  }
}
```

## Felder
- `id`: `<gruppe>-<nnn>`, identisch mit dem Dateinamenanfang.
- `herkunft`: `neu`, `fehler: <Kurzbeschreibung, Datum>` oder `rueckmeldung: <Lauf-ID>`.
- `anfrage`: Absender, Betreff und Text wie eingegangen. Ausschließlich synthetische Daten, keine echten Personen oder Firmen.
- `erwartet.kategorie`: eine von Angebotsanfrage, Ersatzteil, Reklamation, Wartung/Service, Sonstiges, Spam (Auftrag Kapitel 4.2).
- `erwartet.dringlichkeit`: niedrig, normal oder hoch.
- `erwartet.eskalation`: `true` oder `false`; bei `true` nennt `eskalationsgrund` die Regel aus Auftrag Kapitel 4.8, sonst `null`.
- `erwartet.pflicht_artikel`: Artikelnummern, die im Entwurf genannt werden müssen. Nur Nummern aus dem Katalog in `data/`; ist der Katalog noch nicht vorhanden oder unklar, frage nach, erfinde keine Nummern.
- `erwartet.verboten`: Aussagen, die keinesfalls im Entwurf stehen dürfen, als kurze deutsche Beschreibungen.

Bei Manipulationsfällen steht die eingeschleuste Anweisung im Anfragetext, und `verboten` nennt, was daraus nicht folgen darf.
Auswertungsskript und Schema (F23, F24) folgen später; das Format wird dann hier nachgezogen.
