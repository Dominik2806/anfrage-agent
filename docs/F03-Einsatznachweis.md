# F03: Einsatznachweis für Subagents und Skills

Abnahmebedingung F03 (Auftrag Kapitel 3): Alle Subagents und Skills liegen im Repository und wurden
nachweislich mindestens einmal eingesetzt. Stand: 2026-10-05, Branch `feature/F03-subagents-skills`.

| Baustein | Einsätze | Anlass | Beleg |
|---|---|---|---|
| code-reviewer | 2 | Prüfung des Commits 776cf10; danach Prüfung des ganzen Branchs gegenüber main | Nachbesserungen in 04a0bd0; der Bericht des zweiten Einsatzes liegt nicht im Repository |
| security-reviewer | 1 | Prüfung des Commits 776cf10 | Die Befunde führten zu den Gap-Fällen in 5b98c25 |
| test-writer | 2 | Gap-Fälle in `.claude/hooks/test-hooks.ps1`; Hook-Test im Subagent | Gap-Fälle in 5b98c25; der Hook-Test hat keine Datei hinterlassen, sein Ergebnis steht unten |
| adr-schreiben | 1 | Entscheidung zu den Werkzeugen der Reviewer dokumentieren | ADR 0001 in 39a351e |
| eval-fall-anlegen | 1 | Ersten Beispielfall anlegen | Fall `heikel-001` in 1cd45e6 |

## Ergebnisse der Prüfungen

Der Hook `block-env` hat einen Bash-Aufruf des test-writer blockiert. Die Hooks gelten also auch im
Subagent.

Für `block-main-push` im Subagent gibt es keinen Beleg, nur die Annahme, dass er greift, weil beide Hooks
im selben Block der `settings.json` stehen. Ein Versuch, `git push origin main` über den test-writer
auszuführen, kam nicht bis zum Hook: Der test-writer hat den Befehl selbst abgelehnt.
