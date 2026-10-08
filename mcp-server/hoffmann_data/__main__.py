"""Start des Datenservice: python -m hoffmann_data (aus dem Ordner mcp-server).

Ohne gültige Konfiguration bricht der Start mit Exit-Code 1 ab, auch ohne MCP_SERVER_DATABASE_URL.
Auf stderr steht nur der Name der Variablen, nie ihr Wert.
"""

import os
import sys
from collections.abc import Mapping

import uvicorn

from hoffmann_data.config import ConfigError, load_config
from hoffmann_data.server import create_app


def main(env: Mapping[str, str] | None = None) -> None:
    source = os.environ if env is None else env
    try:
        config = load_config(source, require_database=True)
    except ConfigError as exc:
        print(f"Konfigurationsfehler: {exc}", file=sys.stderr)
        raise SystemExit(1) from None

    # Kein Zugriffsprotokoll: Es würde Pfad und Query jeder Anfrage schreiben, auch einer mit falsch
    # platziertem Token.
    uvicorn.run(create_app(config), host=config.host, port=config.port, access_log=False)


if __name__ == "__main__":
    main()
