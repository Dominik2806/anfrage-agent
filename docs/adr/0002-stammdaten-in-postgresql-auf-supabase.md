# ADR 0002: Stammdaten in PostgreSQL auf Supabase

- Status: Angenommen
- Datum: 2026-10-06

## Kontext
Die Stammdaten (Katalog, Kunden, Historie, Rabattregeln) brauchen eine Datenbank. Der Auftrag nennt
PostgreSQL, z. B. Supabase. F05 verlangt, dass ein Befehl die Datenbank befüllt. Die Datenbank wird
bei F30 ohnehin online gebraucht.

## Entscheidung
Die Stammdaten liegen in PostgreSQL auf Supabase. Die kostenlose Stufe genügt. Der Datenservice
greift über eine Serververbindung zu.

## Alternativen
- Lokaler Postgres-Container: verworfen, weil er bei F30 durch eine gehostete Datenbank ersetzt
  werden müsste.
- Reine SQL-Dateien ohne laufende Datenbank: verworfen, weil F05 verlangt, dass ein Befehl die
  Datenbank befüllt.

## Konsequenzen
- Row Level Security ist aktiv, aber ohne Policies. Der Datenservice nutzt eine Serverrolle.
- Zugangsdaten stehen nur in Umgebungsvariablen, die Datei liegt außerhalb des Projekts.
- Mindestversion ist PostgreSQL 15.
- Die Tests der Constraints laufen in der CI gegen einen Postgres-Container.
