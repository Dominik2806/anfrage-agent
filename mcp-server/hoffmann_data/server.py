"""MCP-Server hoffmann-data: acht Schnittstellen aus Auftrag 6.4.

Seit F07 ist search_products echt (Daten aus PostgreSQL, nur lesend). get_product und find_customer
(F07), create_lead und log_activity (F08), beide Resources und der Prompt sind noch Platzhalter: Sie
liefern einen festen Fehler "noch nicht implementiert", nie Scheindaten, und haben keine Parameter.
Fehlertexte enthalten keine Eingabewerte. Es gibt keine Werkzeuge zum Versenden oder Löschen
(Auftrag 5.1). Ohne Datenbank antworten die Lese-Werkzeuge mit "nicht konfiguriert", nie mit Scheindaten.
"""

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from mcp_types import INTERNAL_ERROR
from pydantic import WithJsonSchema
from starlette.types import ASGIApp

from hoffmann_data import catalog
from hoffmann_data.auth import BearerTokenMiddleware
from hoffmann_data.config import LOOPBACK_HOSTS, Config
from hoffmann_data.db import ConnectionSource, Database
from hoffmann_data.limits import DEFAULT_LIMIT, MAX_LIMIT, MAX_QUERY_LENGTH

STREAMABLE_HTTP_PATH = "/mcp"
NOT_IMPLEMENTED = "Diese Schnittstelle ist noch nicht implementiert."

# Eingabeschema von search_products. Die Parameter sind absichtlich als Any mit eigenem JSON-Schema
# deklariert: Das SDK würde sonst bei falschem Typ eine Pydantic-Meldung mit dem Eingabewert zurückgeben.
# So prüft limits.py (feste Meldung ohne Wert); das Schema sagt dem Client trotzdem, was gilt.
QUERY_SCHEMA = {
    "type": "string",
    "title": "Query",
    "minLength": 1,
    "maxLength": MAX_QUERY_LENGTH,
    "description": "Suchtext: Produktname, Umschreibung (z. B. Gurtband) oder Artikelnummer (z. B. FB-1001).",
}
LIMIT_SCHEMA = {
    "type": "integer",
    "title": "Limit",
    "minimum": 1,
    "maximum": MAX_LIMIT,
    "default": DEFAULT_LIMIT,
    "description": "Höchstzahl der Treffer (Standard 5, höchstens 20).",
}
SEARCH_PRODUCTS_DESCRIPTION = (
    "Freitextsuche im Produktkatalog nach Name, Beschreibung (auch Umschreibungen wie Gurtband) oder "
    "Artikelnummer. Liefert Treffer mit Artikelnummer, Name, Kategorie und is_active, die besten zuerst. "
    "Ein exakter Treffer der Artikelnummer steht immer zuerst; inaktive (ausgelaufene) Artikel stehen hinter "
    "den aktiven und sind mit is_active=false gekennzeichnet. Kein Treffer ist eine leere Liste. "
    "Preise und Lieferzeiten liefert nur get_product. Nur Artikelnummern aus dieser Liste verwenden."
)

# Größte erlaubte Anfrage (Auftrag 11.3: Eingaben begrenzen). Das SDK-Standardlimit liegt bei 4 MiB;
# größere Anfragen mit gültigem Token bekommen 413. Eingabelängen je Werkzeug folgen mit F07.
MAX_REQUEST_BODY_BYTES = 256 * 1024

# Dieselben Werte, die das SDK für Loopback selbst einsetzt. Wir setzen sie ausdrücklich, damit der
# Schutz nicht davon abhängt, welchen Host der Aufrufer übergibt.
_LOOPBACK_ALLOWED_HOSTS = ("127.0.0.1:*", "localhost:*", "[::1]:*")
_LOOPBACK_ALLOWED_ORIGINS = ("http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*")


def create_mcp_server(database: ConnectionSource | None = None) -> MCPServer:
    server = MCPServer(
        "hoffmann-data",
        instructions=(
            "Datenservice der Hoffmann Maschinenbau GmbH. search_products liest den Katalog; "
            "die übrigen Schnittstellen sind noch Platzhalter."
        ),
    )

    # Sync-Funktion: Das SDK führt sie in einem Worker-Thread aus, die Datenbankabfrage blockiert die Schleife nicht.
    @server.tool(name="search_products", description=SEARCH_PRODUCTS_DESCRIPTION)
    def search_products(
        query: Annotated[Any, WithJsonSchema(QUERY_SCHEMA)],
        limit: Annotated[Any, WithJsonSchema(LIMIT_SCHEMA)] = DEFAULT_LIMIT,
    ) -> catalog.SearchResult:
        return catalog.search_products(database, query, limit)

    @server.tool(
        name="get_product",
        description="Details, Listenpreis und Lieferzeit zu einer Artikelnummer (Platzhalter).",
    )
    def get_product() -> str:
        raise ToolError(NOT_IMPLEMENTED)

    @server.tool(
        name="find_customer",
        description="Kunde über Firma oder E-Mail-Domain finden (Platzhalter).",
    )
    def find_customer() -> str:
        raise ToolError(NOT_IMPLEMENTED)

    @server.tool(
        name="create_lead", description="Neuen Lead anlegen, nur nach Freigabe (Platzhalter)."
    )
    def create_lead() -> str:
        raise ToolError(NOT_IMPLEMENTED)

    @server.tool(
        name="log_activity", description="Aktivität zu einem Kunden protokollieren (Platzhalter)."
    )
    def log_activity() -> str:
        raise ToolError(NOT_IMPLEMENTED)

    @server.resource(
        "policy://tonalitaet",
        name="policy_tonalitaet",
        description="Tonalitätsleitfaden (Platzhalter).",
        mime_type="text/markdown",
    )
    def policy_tonalitaet() -> str:
        raise ResourceError(NOT_IMPLEMENTED)

    @server.resource(
        "policy://rabatte",
        name="policy_rabatte",
        description="Rabatt- und Eskalationsregeln (Platzhalter).",
        mime_type="text/markdown",
    )
    def policy_rabatte() -> str:
        raise ResourceError(NOT_IMPLEMENTED)

    @server.prompt(
        name="antwort_entwurf", description="Vorlage für den Antwortentwurf (Platzhalter)."
    )
    def antwort_entwurf() -> str:
        # MCPError statt einer einfachen Ausnahme: Das SDK ersetzt bei Prompts jede andere Ausnahme durch
        # die Allgemeinmeldung "Error rendering prompt ..." (prompts/base.py), unser Text ginge verloren.
        # Eine MCPError reicht es mit Code und Text unverändert an den Client durch.
        raise MCPError(code=INTERNAL_ERROR, message=NOT_IMPLEMENTED)

    return server


def transport_security(config: Config) -> TransportSecuritySettings:
    """Host-/Origin-Schutz, immer ausdrücklich gesetzt (das SDK schaltet ihn nur für Loopback selbst ein)."""
    if config.host in LOOPBACK_HOSTS:
        hosts = list(dict.fromkeys((*_LOOPBACK_ALLOWED_HOSTS, *config.allowed_hosts)))
        origins = list(_LOOPBACK_ALLOWED_ORIGINS)
    else:
        hosts = list(config.allowed_hosts)
        origins = []  # Browser-Anfragen mit Origin sind hier nicht vorgesehen, der Agent sendet keinen.
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=hosts,
        allowed_origins=origins,
    )


def create_app(config: Config, database: ConnectionSource | None = None) -> ASGIApp:
    """Die SDK-App für Streamable HTTP, außen umhüllt von der Token-Prüfung (ADR 0003).

    Ohne `database` wird sie aus `config.database_url` gebaut (der Konstruktor verbindet nicht); ohne beides
    antworten die Lese-Werkzeuge mit "nicht konfiguriert".
    """
    if database is None and config.database_url is not None:
        database = Database(config.database_url)
    sdk_app = create_mcp_server(database).streamable_http_app(
        streamable_http_path=STREAMABLE_HTTP_PATH,
        json_response=False,
        stateless_http=True,
        transport_security=transport_security(config),
        host=config.host,
        max_request_body_size=MAX_REQUEST_BODY_BYTES,
    )
    return BearerTokenMiddleware(sdk_app, config.token)
