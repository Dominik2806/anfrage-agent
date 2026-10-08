# mcp-server

Datenservice „hoffmann-data“ (Python, MCP, Streamable HTTP). Stand F06: Grundgerüst mit Zugangsschutz.
Die acht Schnittstellen aus dem Auftrag (Kapitel 6.4) sind angemeldet, aber noch Platzhalter.
Es gibt keine Datenbankanbindung.

## Einrichtung
Aus dem Hauptordner des Projekts:

```
python -m pip install -r mcp-server/requirements.txt
```

Unter Windows mit der virtuellen Umgebung: `.\.venv\Scripts\python.exe -m pip install -r mcp-server/requirements.txt`.

## Start
Aus dem Ordner `mcp-server`:

```
python -m hoffmann_data
```

Der Server lauscht standardmäßig auf `127.0.0.1:8000`, der Endpunkt ist `/mcp`. Ohne gültiges Token
bricht der Start mit Exit-Code 1 ab, auf stderr steht nur der Name der Variablen.

## Umgebungsvariablen
Die Werte kommen aus der Umgebung, nie aus einer Datei im Projekt. Vorlage: `.env.example` (ohne Werte).

| Variable | Pflicht | Bedeutung |
|---|---|---|
| `MCP_SERVER_TOKEN` | ja | Zugangstoken. Mindestens 32 Zeichen, nur druckbare ASCII-Zeichen, keine Leerzeichen, Tabulatoren oder Zeilenumbrüche. Ein ungültiges Token wird abgelehnt, nicht gekürzt. |
| `MCP_SERVER_HOST` | nein | Adresse, an die der Server bindet. Standard `127.0.0.1`. Andere Werte als `127.0.0.1`, `localhost` und `::1` brauchen `MCP_SERVER_ALLOWED_HOSTS`. |
| `MCP_SERVER_PORT` | nein | Port von 1 bis 65535. Standard `8000`. |
| `MCP_SERVER_ALLOWED_HOSTS` | nur bei Host außer Loopback | Erlaubte Werte des `Host`-Headers, kommagetrennt. Siehe unten. |

Token erzeugen:

```
python -c "import secrets; print(secrets.token_urlsafe(32))"
```

Das Token gehört in eine Datei oder einen Schlüsselspeicher außerhalb des Projektordners, nie ins Repository.

## Zugangsschutz
- Jede Anfrage braucht genau den Header `Authorization: Bearer <token>`. Groß-/Kleinschreibung des Schemas
  und zusätzliche Leerzeichen werden abgelehnt. Das Token in der URL gilt nicht.
- Jede Route ohne gültiges Token antwortet mit 401 und immer demselben Text, auch Pfade, die es nicht gibt.
  Der Vergleich läuft in konstanter Zeit (`hmac.compare_digest`).
- WebSocket-Verbindungen werden abgelehnt.
- Der Host-/Origin-Schutz des SDKs ist immer an. Falscher Host: 421, falscher Origin: 403.
- Kein Zugriffsprotokoll: uvicorn läuft mit `access_log=False`, damit Pfad und Query (und ein falsch
  platziertes Token) nicht im Protokoll landen. Das SDK protokolliert abgelehnte Host-/Origin-Werte,
  das ist Fremdtext und enthält nie das Token.
- Der Server arbeitet zustandslos (keine Sitzungen). Eine Anfrage braucht kein `initialize`.
  Aufruf: `POST /mcp` mit `Content-Type: application/json` und `Accept: application/json, text/event-stream`;
  die Antwort kann ein Ereignisstrom (SSE) sein.

### `MCP_SERVER_ALLOWED_HOSTS`
Die Liste nennt die genauen Werte, die im `Host`-Header ankommen dürfen, z. B. `daten.example.org`.
- Mit `:*` am Ende gilt jeder Port: `daten.example.org:*`.
- Der Port muss mit angegeben werden, wenn er nicht 80 (http) oder 443 (https) ist, z. B. `daten.example.org:8000`.
- Einträge sind durch Kommas getrennt. Leere Einträge und Leerzeichen innerhalb eines Eintrags sind ungültig.
- Auf `127.0.0.1`, `localhost` und `::1` gelten die Loopback-Werte immer, die Liste ergänzt sie.
- Bei einem Host außer Loopback gilt nur die Liste. Anfragen mit einem `Origin`-Header (Browser) werden dort
  mit 403 abgelehnt, der Agent sendet keinen.

Ohne die Liste startet der Server nur auf `127.0.0.1`.

## Schnittstellen (Platzhalter)
Alle acht haben eine kurze Beschreibung und keine Parameter (die Signaturen kommen mit F07 bis F09).
Sie liefern nie Scheindaten, sondern den festen Fehler „Diese Schnittstelle ist noch nicht implementiert.“,
ohne Eingabewerte.

| Typ | Name | Verhalten in F06 |
|---|---|---|
| Tool | `search_products` | Ergebnis mit `isError: true` und dem festen Text |
| Tool | `get_product` | wie oben |
| Tool | `find_customer` | wie oben |
| Tool | `create_lead` | wie oben |
| Tool | `log_activity` | wie oben |
| Resource | `policy://tonalitaet` | JSON-RPC-Fehler `-32603` mit dem festen Text |
| Resource | `policy://rabatte` | wie oben |
| Prompt | `antwort_entwurf` | JSON-RPC-Fehler `-32603` mit dem festen Text (`MCPError`) |

Es gibt keine Werkzeuge zum Versenden oder Löschen. Fortschrittsmeldungen bei länger laufenden Aufrufen
(Auftrag 6.4) sind nicht Teil von F06, sie kommen mit F10. Der Transport (zustandslos, Ereignisstrom) lässt
sie zu (ADR 0003).

## Hosting (F30)
Der Server spricht selbst kein TLS. Beim Hosting muss der Hoster TLS davor setzen, sonst läuft das Token
unverschlüsselt. Es gibt ein gemeinsames Token für alle Clients, ohne Rotation. Bekannte Grenzen stehen in
`docs/adr/0003-token-pruefung-eigene-middleware.md`.

## Hinweis zu PowerShell 5.1
Der Server antwortet in UTF-8, nennt aber keinen Zeichensatz. Windows PowerShell 5.1 zeigt solche Antworten
als Latin-1 an, Umlaute wirken dann falsch (z. B. „ü“ als „Ã¼“). Der Server ist korrekt, es ist eine Eigenheit
der Anzeige. Die Antwort mit einem Werkzeug lesen, das UTF-8 voraussetzt, oder in PowerShell 7 prüfen.

## Tests
Aus dem Hauptordner:

```
python -m pytest tests/mcp_server
```

Die Tests brauchen keine Datenbank und kein Netzwerk. Die App läuft im Speicher, es wird kein Port geöffnet.
