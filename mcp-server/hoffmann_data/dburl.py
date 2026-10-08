"""Prüfung der Datenbank-URL des Datenservice (F07).

Gekürzte Kopie der URL-Regeln aus db/seed/guard.py: Der Datenservice läuft aus seinem Ordner und darf
db.seed nicht importieren. Der Paritätstest tests/mcp_server/test_dburl_parity.py hält beide zusammen.

check_database_url gibt einen festen Fehlertext oder None zurück und wirft nie. Der Text enthält nie
einen Teil der URL (Benutzer, Passwort, Host, Datenbankname). Die URL wird nicht gekürzt oder
bereinigt: Was nicht genau so gültig ist, wie es dasteht, wird abgelehnt.
"""

import re
from collections.abc import Mapping
from urllib.parse import parse_qs, unquote, urlsplit

_PREFIXES = ("postgresql://", "postgres://")
_LOCAL_HOSTS = ("localhost", "127.0.0.1", "::1")
_SECURE_SSLMODES = ("require", "verify-ca", "verify-full")
_DBNAME = re.compile(r"[A-Za-z0-9_.\-]{1,63}")
# Parameter und Umgebungsvariablen, die den geprüften Host oder die Datenbank überstimmen würden
_OVERRIDING_PARAMS = ("host", "hostaddr", "service", "dbname")
_OVERRIDING_ENV = ("PGHOSTADDR", "PGSERVICE")

_UNREADABLE = "Die URL ist nicht lesbar."
_TLS = "Für diesen Host muss die URL den Parameter sslmode mit require, verify-ca oder verify-full enthalten."


def check_database_url(url: str | None, environ: Mapping[str, str]) -> str | None:
    """Fehlertext, wenn die URL nicht angenommen wird, sonst None."""
    try:
        return _problem(url, environ)
    except ValueError:  # urlsplit, Port und strenges Dekodieren werfen ValueError
        return _UNREADABLE


def _problem(url: str | None, environ: Mapping[str, str]) -> str | None:
    if url is None or not url.strip():
        return "Die URL fehlt oder ist leer."
    if not url.startswith(_PREFIXES):
        return "Die URL muss mit postgresql:// beginnen."
    # urlsplit entfernt Leerzeichen und Zeilenumbrüche und ändert Unicode, libpq nicht
    if any(not 0x21 <= ord(char) <= 0x7E for char in url):
        return "Die URL darf nur sichtbare ASCII-Zeichen enthalten (Sonderzeichen prozentkodieren)."
    parts = urlsplit(url)
    if "#" in url:
        return "Die URL enthält ein #. Sonderzeichen im Passwort müssen prozentkodiert werden."
    # libpq sucht das @ der Anmeldung bis zum ersten /, urlsplit nur bis zum ersten / oder ?
    authority = url.partition("://")[2].partition("/")[0]
    if authority.count("@") > 1:
        return "Die URL enthält mehr als ein @. Ein @ im Passwort muss als %40 geschrieben werden."
    if "?" in authority.partition("@")[0] and "@" in authority:
        return "Die URL hat einen unklaren Aufbau: Ein @ steht hinter einem ? vor dem ersten /."
    if "," in parts.netloc.rpartition("@")[2]:
        return "Die URL darf nur einen Host enthalten."
    host = parts.hostname
    if not host:
        return "Die URL enthält keinen Host (Unix-Sockets sind nicht erlaubt)."
    if "%" in host:
        return "Die URL darf im Host keine Prozentkodierung enthalten."
    _ = parts.port  # wirft ValueError bei ungültigem Port
    names = {name.lower() for name in parse_qs(parts.query, keep_blank_values=True)}
    if names & set(_OVERRIDING_PARAMS):
        return "Die URL darf host, hostaddr, service und dbname nicht als Parameter enthalten."
    dbname = unquote(parts.path.removeprefix("/"), errors="strict")
    if not _DBNAME.fullmatch(dbname):
        return "Die URL enthält keinen gültigen Datenbanknamen (Buchstaben, Ziffern, _, . und -)."
    if any(name in environ for name in _OVERRIDING_ENV):
        return "PGHOSTADDR und PGSERVICE würden den geprüften Host überstimmen und dürfen nicht gesetzt sein."
    if host not in _LOCAL_HOSTS:
        modes = parse_qs(parts.query, keep_blank_values=True).get("sslmode", [])
        if len(modes) != 1 or modes[0] not in _SECURE_SSLMODES:
            return _TLS
    return None
