# ADR 0003: Token-Prüfung des Datenservice durch eigene Middleware, ohne Sitzungen

- Status: Angenommen
- Datum: 2026-10-08

## Kontext
F06 verlangt, dass der Datenservice „hoffmann-data“ nur mit gültigem Zugangstoken antwortet und seine
Schnittstellen auflistet (Auftrag 3.2, 6.4, 11.3). Es gibt ein einziges, festes Token für alle Clients.
Geprüft wurde das installierte SDK `mcp` 2.3.0 (Klasse `MCPServer`, `mcp.server.mcpserver`).
Das SDK bietet `AuthSettings` mit `TokenVerifier`. `AuthSettings.issuer_url` und `resource_server_url`
sind dort Pflichtfelder (OAuth). Mit `resource_server_url` legt das SDK zusätzlich Routen für
OAuth-Metadaten an, die ohne Token erreichbar sind. Dasselbe gilt für Routen über `custom_route`.
Zusätzlich muss der Transport entschieden werden: mit oder ohne Sitzungen, JSON oder Strom.
F10 meldet bei länger laufenden Aufrufen den Fortschritt (Auftrag 6.4) und braucht dafür Streaming.

## Entscheidung
1. Eine eigene, kleine ASGI-Middleware legt sich außen um die App des SDKs
   (`MCPServer.streamable_http_app`). Sie prüft bei jeder HTTP-Anfrage auf jedem Pfad den Header
   `Authorization: Bearer <token>` mit `hmac.compare_digest`. Jeder Fehlerfall liefert dieselbe 401-Antwort
   mit festem Text. Das Token steht nie in Antwort, Fehlermeldung oder Log.
   Der Header muss genau `Authorization: Bearer <token>` lauten; Groß-/Kleinschreibung des Schemas und
   zusätzliche Leerzeichen werden bewusst abgelehnt. WebSocket-Verbindungen werden ohne Token abgewiesen.
2. Der Start bricht ab, wenn `MCP_SERVER_TOKEN` fehlt, leer oder kürzer als 32 Zeichen ist, ein
   Leerzeichen oder einen Zeilenumbruch enthält oder nicht nur aus druckbaren ASCII-Zeichen besteht.
   Das Token wird nie gekürzt oder bereinigt.
3. Host-/Origin-Schutz des SDKs (`TransportSecuritySettings`) wird immer ausdrücklich gesetzt. Das SDK schaltet
   ihn von selbst nur für `127.0.0.1`, `localhost` und `::1` ein. Andere Hosts starten nur mit
   `MCP_SERVER_ALLOWED_HOSTS`.
4. `stateless_http=True` und `json_response=False`: keine Sitzungen auf dem Server, jede Anfrage steht für sich,
   die Antwort darf ein Ereignisstrom sein. Das lässt Fortschrittsmeldungen für F10 zu.

## Alternativen
- SDK-Auth (`TokenVerifier` + `AuthSettings`): verworfen. Ein festes Token hat keinen OAuth-Aussteller, die
  erfundene `issuer_url` wäre irreführend, und die Metadaten-Routen widersprechen „keine Route ohne Token“.
  Fehlertexte und Antwortformat lägen nicht bei uns.
- `json_response=True`: verworfen. Jede Antwort wäre ein einzelnes JSON-Dokument, Fortschrittsmeldungen
  während eines Aufrufs wären nicht möglich. F10 müsste die Wahl rückgängig machen.
- Zustandsbehaftete Sitzungen: verworfen. Der Server braucht für F06 bis F10 keinen Zustand zwischen Anfragen;
  Sitzungen würden Speicher, Ablauf und Zuordnung zum Token zusätzlich absichern müssen.
- Token in URL oder Query: verworfen, weil URLs in Protokollen und Verläufen landen.

## Konsequenzen
- Eigener Sicherheitscode (~40 Zeilen) mit eigenen Tests und `security-reviewer` vor dem Pull Request.
- Laut Quelltext (`streamable_http_manager.py`) erlaubt der zustandslose Modus Benachrichtigungen, aber keine
  Anfragen des Servers an den Client. Fortschrittsmeldungen sind Benachrichtigungen. F10 bestätigt das mit einem
  Test; die Wahl hier blockiert F10 nicht, solange `json_response=False` bleibt.
- Der Server bietet keine Rückfragen an den Client (Elicitation, Sampling). Das braucht der Auftrag nicht.
- Bekannte Grenzen: ein gemeinsames Token ohne Rotation; Übertragung unverschlüsselt, daher beim Hosting (F30)
  TLS am Hoster zwingend; das SDK schreibt abgelehnte Host-/Origin-Werte ins Log (kein Token, aber
  Fremdtext, daher nicht als vertrauenswürdig behandeln).
- Spätere Umstellung auf OAuth bleibt möglich, indem die Middleware durch `AuthSettings` ersetzt wird.
