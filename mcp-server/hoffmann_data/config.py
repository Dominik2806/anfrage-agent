"""Konfiguration aus Umgebungsvariablen (F06).

Fehlermeldungen nennen nur den Namen der Variablen, nie ihren Wert. Das Token wird nie gekürzt
oder bereinigt: Ist es nicht genau so gültig, wie es steht, startet der Server nicht.
"""

from collections.abc import Mapping
from dataclasses import dataclass, field

TOKEN_VAR = "MCP_SERVER_TOKEN"
HOST_VAR = "MCP_SERVER_HOST"
PORT_VAR = "MCP_SERVER_PORT"
ALLOWED_HOSTS_VAR = "MCP_SERVER_ALLOWED_HOSTS"

MIN_TOKEN_LENGTH = 32
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


def load_config(env: Mapping[str, str]) -> Config:
    """Liest und prüft die Konfiguration. Wirft ConfigError, liest nie die echte Umgebung."""
    token = _read_token(env)
    host = _read_host(env)
    port = _read_port(env)
    allowed_hosts = _read_allowed_hosts(env)

    if host not in LOOPBACK_HOSTS and not allowed_hosts:
        raise ConfigError(
            f"{HOST_VAR} ist keine Loopback-Adresse: {ALLOWED_HOSTS_VAR} muss die erlaubten "
            "Host-Werte nennen (kommagetrennt). Ohne die Liste startet der Server nur auf 127.0.0.1."
        )
    return Config(token=token, host=host, port=port, allowed_hosts=allowed_hosts)


def _read_token(env: Mapping[str, str]) -> str:
    token = env.get(TOKEN_VAR, "")
    # Nur druckbare ASCII-Zeichen ohne Leerzeichen (33..126): schließt Leerzeichen, Tabulator,
    # Zeilenumbruch und Nicht-ASCII aus. Der Wert gehört unverändert in einen HTTP-Header.
    if len(token) < MIN_TOKEN_LENGTH or not all(33 <= ord(c) <= 126 for c in token):
        raise ConfigError(
            f"{TOKEN_VAR} fehlt oder ist ungültig: mindestens {MIN_TOKEN_LENGTH} Zeichen, "
            "nur druckbare ASCII-Zeichen, keine Leerzeichen und keine Zeilenumbrüche."
        )
    return token


def _read_host(env: Mapping[str, str]) -> str:
    host = env.get(HOST_VAR, DEFAULT_HOST)
    if not host or any(c.isspace() for c in host):
        raise ConfigError(f"{HOST_VAR} ist leer oder enthält Leerzeichen.")
    return host


def _read_port(env: Mapping[str, str]) -> int:
    if PORT_VAR not in env:
        return DEFAULT_PORT
    raw = env[PORT_VAR]
    if not (raw.isascii() and raw.isdigit()) or not 1 <= int(raw) <= 65535:
        raise ConfigError(f"{PORT_VAR} muss eine Zahl von 1 bis 65535 sein.")
    return int(raw)


def _read_allowed_hosts(env: Mapping[str, str]) -> tuple[str, ...]:
    if ALLOWED_HOSTS_VAR not in env:
        return ()
    entries = tuple(part.strip() for part in env[ALLOWED_HOSTS_VAR].split(","))
    if any(not entry or any(c.isspace() for c in entry) for entry in entries):
        raise ConfigError(
            f"{ALLOWED_HOSTS_VAR} enthält einen leeren Eintrag oder Leerzeichen innerhalb eines Eintrags."
        )
    return entries
