"""MCP-Server hoffmann-data (F06): acht Schnittstellen aus Auftrag 6.4 als Platzhalter.

Alle acht liefern einen festen Fehler "noch nicht implementiert", nie Scheindaten. Die Platzhalter
haben keine Parameter, die Signaturen kommen mit den echten Werkzeugen ab F07. Fehlertexte enthalten
keine Eingabewerte. Es gibt keine Werkzeuge zum Versenden oder Löschen (Auftrag 5.1).
"""

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ResourceError, ToolError
from mcp.server.transport_security import TransportSecuritySettings
from mcp.shared.exceptions import MCPError
from mcp_types import INTERNAL_ERROR
from starlette.types import ASGIApp

from hoffmann_data.auth import BearerTokenMiddleware
from hoffmann_data.config import LOOPBACK_HOSTS, Config

STREAMABLE_HTTP_PATH = "/mcp"
NOT_IMPLEMENTED = "Diese Schnittstelle ist noch nicht implementiert."

# Dieselben Werte, die das SDK für Loopback selbst einsetzt. Wir setzen sie ausdrücklich, damit der
# Schutz nicht davon abhängt, welchen Host der Aufrufer übergibt.
_LOOPBACK_ALLOWED_HOSTS = ("127.0.0.1:*", "localhost:*", "[::1]:*")
_LOOPBACK_ALLOWED_ORIGINS = ("http://127.0.0.1:*", "http://localhost:*", "http://[::1]:*")


def create_mcp_server() -> MCPServer:
    server = MCPServer(
        "hoffmann-data",
        instructions="Datenservice der Hoffmann Maschinenbau GmbH. Die Schnittstellen sind noch Platzhalter.",
    )

    @server.tool(name="search_products", description="Freitextsuche im Katalog (Platzhalter).")
    def search_products() -> str:
        raise ToolError(NOT_IMPLEMENTED)

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


def create_app(config: Config) -> ASGIApp:
    """Die SDK-App für Streamable HTTP, außen umhüllt von der Token-Prüfung (ADR 0003)."""
    sdk_app = create_mcp_server().streamable_http_app(
        streamable_http_path=STREAMABLE_HTTP_PATH,
        json_response=False,
        stateless_http=True,
        transport_security=transport_security(config),
        host=config.host,
    )
    return BearerTokenMiddleware(sdk_app, config.token)
