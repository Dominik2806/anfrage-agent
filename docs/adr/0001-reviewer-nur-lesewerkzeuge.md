# ADR 0001: Reviewer-Subagents erhalten nur lesende Werkzeuge

- Status: Angenommen
- Datum: 2026-10-05

## Kontext
Die Subagents code-reviewer und security-reviewer prüfen Änderungen und sollen nichts verändern
(Auftrag Kapitel 9.1, Feature F03). Ein Prüfer mit Schreib- oder Shell-Rechten ist keine Grenze,
wenn die Einschränkung nur im Prompt steht. Eine Regel im Text ist ein Wunsch, eine Regel im System
ist eine Grenze.

## Entscheidung
code-reviewer und security-reviewer erhalten nur die Werkzeuge Read, Grep und Glob.

## Alternativen
- Bash nur für lesende Git-Befehle: verworfen, weil sich Bash technisch nicht auf lesend
  beschränken lässt.
- Einschränkung nur im Prompt, ohne Werkzeugsperre: verworfen, weil sie ein Wunsch ist und keine Grenze.

## Konsequenzen
- Der Aufrufer muss Diff oder Dateiliste übergeben.
- Branch und Commit-Nachrichten kann der code-reviewer nur prüfen, wenn der Aufrufer sie mitliefert,
  weil er kein `git log` ausführen kann.
