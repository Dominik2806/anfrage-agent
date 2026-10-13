"""Konfiguration aus Umgebungsvariablen (F06).

Fehlermeldungen nennen nur den Namen der Variablen, nie ihren Wert. Das Token wird nie gekürzt
oder bereinigt: Ist es nicht genau so gültig, wie es steht, startet der Server nicht.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

from hoffmann_data.dburl import check_database_url

TOKEN_VAR = "MCP_SERVER_TOKEN"
HOST_VAR = "MCP_SERVER_HOST"
PORT_VAR = "MCP_SERVER_PORT"
ALLOWED_HOSTS_VAR = "MCP_SERVER_ALLOWED_HOSTS"
DATABASE_URL_VAR = "MCP_SERVER_DATABASE_URL"
# Schreibweg (F08, ADR 0005): beide Variablen gehören zusammen, beide fehlen = kein Schreibweg
WRITE_DATABASE_URL_VAR = "MCP_SERVER_WRITE_DATABASE_URL"
WRITE_TOKEN_VAR = "MCP_SERVER_WRITE_TOKEN"

MIN_TOKEN_LENGTH = 32
MIN_DISTINCT_TOKEN_CHARS = 10
DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 8000
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})


class ConfigError(ValueError):
    """Ungültige oder fehlende Konfiguration. Die Meldung enthält nie einen Wert."""


@dataclass(frozen=True)
class Config:
    token: str = field(repr=False)
    host: str
    port: int
    allowed_hosts: tuple[str, ...]
    # Verbindung zur Datenbank (F07); nie in repr, da sie das Passwort enthält
    database_url: str | None = field(default=None, repr=False)
    # Schreibweg (F08): Datenbankverbindung und eigenes Token; beides nie in repr
    write_database_url: str | None = field(default=None, repr=False)
    write_token: str | None = field(default=None, repr=False)

    @property
    def write_enabled(self) -> bool:
        """Der Schreibweg ist konfiguriert: URL und Token sind beide gesetzt."""
        return self.write_database_url is not None and self.write_token is not None


def load_config(env: Mapping[str, str], *, require_database: bool = False) -> Config:
    """Liest und prüft die Konfiguration. Wirft ConfigError, liest nie die echte Umgebung.

    require_database: Der Start des Servers (main) verlangt MCP_SERVER_DATABASE_URL; Tests, die die App
    ohne Datenbank bauen, brauchen sie nicht. Ist die Variable gesetzt, wird sie immer geprüft.
    """
    token = _read_token(env)
    host = _read_host(env)
    port = _read_port(env)
    allowed_hosts = _read_allowed_hosts(env)

    if host not in LOOPBACK_HOSTS and not allowed_hosts:
        raise ConfigError(
            f"{HOST_VAR} ist keine Loopback-Adresse: {ALLOWED_HOSTS_VAR} muss die erlaubten "
            "Host-Werte nennen (kommagetrennt). Ohne die Liste startet der Server nur auf 127.0.0.1."
        )
    database_url = _read_database_url(env, DATABASE_URL_VAR, require_database)
    # Der Schreibweg wird zuletzt geprüft: Lese-Token, Host, Port, erlaubte Hosts und Lese-URL kommen zuerst
    write_database_url, write_token = _read_write_path(env, token)
    return Config(
        token=token,
        host=host,
        port=port,
        allowed_hosts=allowed_hosts,
        database_url=database_url,
        write_database_url=write_database_url,
        write_token=write_token,
    )


def _check_token(token: str, name: str) -> None:
    """Prüft ein Zugangstoken. Wirft ConfigError mit dem Namen der Variablen, nie mit dem Wert."""
    # Nur druckbare ASCII-Zeichen ohne Leerzeichen (33..126): schließt Leerzeichen, Tabulator,
    # Zeilenumbruch und Nicht-ASCII aus. Der Wert gehört unverändert in einen HTTP-Header.
    # Die Mindestzahl verschiedener Zeichen weist triviale Tokens wie "a" * 32 ab.
    if (
        len(token) < MIN_TOKEN_LENGTH
        or len(set(token)) < MIN_DISTINCT_TOKEN_CHARS
        or not all(33 <= ord(c) <= 126 for c in token)
    ):
        raise ConfigError(
            f"{name} fehlt oder ist ungültig: mindestens {MIN_TOKEN_LENGTH} Zeichen, "
            f"davon mindestens {MIN_DISTINCT_TOKEN_CHARS} verschiedene, "
            "nur druckbare ASCII-Zeichen, keine Leerzeichen und keine Zeilenumbrüche."
        )


def _read_token(env: Mapping[str, str]) -> str:
    token = env.get(TOKEN_VAR, "")
    _check_token(token, TOKEN_VAR)
    return token


def _optional(env: Mapping[str, str], name: str) -> str | None:
    """Wert einer optionalen Variable; leer oder nur Leerraum gilt als nicht gesetzt (None)."""
    raw = env.get(name)
    if raw is None or not raw.strip():
        return None
    return raw


def _read_host(env: Mapping[str, str]) -> str:
    host = _optional(env, HOST_VAR)
    if host is None:
        return DEFAULT_HOST
    if any(c.isspace() for c in host):
        raise ConfigError(f"{HOST_VAR} enthält Leerzeichen.")
    return host


def _read_port(env: Mapping[str, str]) -> int:
    raw = _optional(env, PORT_VAR)
    if raw is None:
        return DEFAULT_PORT
    if not (raw.isascii() and raw.isdigit()) or not 1 <= int(raw) <= 65535:
        raise ConfigError(f"{PORT_VAR} muss eine Zahl von 1 bis 65535 sein.")
    return int(raw)


def _read_allowed_hosts(env: Mapping[str, str]) -> tuple[str, ...]:
    raw = _optional(env, ALLOWED_HOSTS_VAR)
    if raw is None:
        return ()
    entries = tuple(part.strip() for part in raw.split(","))
    if any(not entry or any(c.isspace() for c in entry) for entry in entries):
        raise ConfigError(
            f"{ALLOWED_HOSTS_VAR} enthält einen leeren Eintrag oder Leerzeichen innerhalb eines Eintrags."
        )
    return entries


def _read_database_url(env: Mapping[str, str], name: str, required: bool) -> str | None:
    raw = _optional(env, name)
    if raw is None:
        if required:
            raise ConfigError(f"{name} fehlt: Der Server braucht die Datenbankverbindung.")
        return None
    # Die URL wird nie gekürzt oder bereinigt. Der Text von check_database_url enthält keinen Wert.
    problem = check_database_url(raw, env)
    if problem is not None:
        raise ConfigError(f"{name} ist ungültig: {problem}")
    return raw


def _read_write_path(env: Mapping[str, str], read_token: str) -> tuple[str | None, str | None]:
    """Schreib-URL und Schreib-Token (ADR 0005 Punkt 2) oder (None, None), wenn beide fehlen.

    Leer oder nur Leerraum gilt als nicht gesetzt. Reihenfolge: Vollständigkeit, Schreib-Token (gleiche Regeln
    wie das Lese-Token), Schreib-URL (gleiche Regeln wie die Lese-URL), Token nicht gleich dem Lese-Token.
    Die Meldungen nennen nur Variablennamen, nie einen Wert.
    """
    raw_url = _optional(env, WRITE_DATABASE_URL_VAR)
    raw_token = _optional(env, WRITE_TOKEN_VAR)
    if raw_url is None and raw_token is None:
        return None, None
    if raw_url is None or raw_token is None:
        raise ConfigError(
            f"{WRITE_DATABASE_URL_VAR} und {WRITE_TOKEN_VAR} gehören zusammen: "
            "Entweder beide setzen oder beide weglassen."
        )
    _check_token(raw_token, WRITE_TOKEN_VAR)
    write_url = _read_database_url(env, WRITE_DATABASE_URL_VAR, True)
    if raw_token == read_token:
        raise ConfigError(
            f"{WRITE_TOKEN_VAR} und {TOKEN_VAR} dürfen nicht gleich sein: "
            "Lese- und Schreibweg brauchen verschiedene Token."
        )
    return write_url, raw_token
