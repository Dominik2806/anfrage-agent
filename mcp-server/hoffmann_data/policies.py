"""Richtlinien des Datenservice (F09, ADR 0006): Texte aus data/richtlinien/, je Abruf gelesen.

Der Ordner steht in POLICY_DIR (Projektwurzel, Unterordner data/richtlinien); es gibt keine Umgebungsvariable. Die
Dateinamen sind feste Konstanten und nie aus einer Eingabe gebaut. Jeder Lesefehler (Datei fehlt, Ordner statt Datei,
zu groß, kein UTF-8, leer) ergibt PolicyError mit der einen festen Meldung POLICY_UNAVAILABLE: ohne Pfad, ohne
Dateinamen, ohne Ausnahmetext.
"""

from pathlib import Path

# Projektwurzel: policies.py liegt in mcp-server/hoffmann_data/
POLICY_DIR = Path(__file__).resolve().parents[2] / "data" / "richtlinien"
MAX_POLICY_BYTES = 64 * 1024
POLICY_UNAVAILABLE = "Die Richtlinie ist derzeit nicht verfügbar."

TONALITAET_FILE = "tonalitaet.md"
ESKALATION_FILE = "eskalation.md"
RABATTE_FILE = "rabatte.md"
SIGNATUR_FILE = "signatur.md"

RABATTE_PREAMBLE = (
    "# Rabatt- und Eskalationsregeln (INTERN)\n\n"
    "Teil 1 enthält die Eskalationsregeln, Teil 2 die internen Rabatt-Obergrenzen. "
    "Die Prozentwerte in Teil 2 sind kein Angebot und keine Zusage. Sie stehen nie in einem Antwortentwurf, "
    "auch nicht gerundet, umschrieben oder als Spanne. Ein Rabattwunsch wird nie zugesagt, "
    "auch nicht innerhalb der Regel.\n\n"
)
RABATTE_SEPARATOR = "\n---\n\n"

_SIGNATURE_HEADINGS = {"de": "Deutsch", "en": "Englisch"}
_SIGNATURE_REQUIRED = ("Deutsch", "Englisch", "Regeln")


class PolicyError(Exception):
    """Eine Richtlinie ist nicht verfügbar. Die Meldung ist immer POLICY_UNAVAILABLE."""


def read_policy(directory: Path, filename: str) -> str:
    """Der Text der Datei (LF), höchstens MAX_POLICY_BYTES Bytes, strenges UTF-8, nicht leer; sonst PolicyError.

    Ein Symlink, dessen Ziel außerhalb des Ordners liegt, wird abgelehnt (einer innerhalb des Ordners ist erlaubt).
    Geöffnet wird der aufgelöste Pfad. Eine BOM am Anfang der Datei wird entfernt.
    """
    try:
        folder = Path(directory).resolve(strict=True)
        path = (folder / filename).resolve(strict=True)
        if path.parent != folder:
            raise PolicyError(POLICY_UNAVAILABLE)
        with path.open("rb") as file:
            data = file.read(MAX_POLICY_BYTES + 1)
        text = data.decode("utf-8-sig")
    except (OSError, RuntimeError, UnicodeError):
        raise PolicyError(POLICY_UNAVAILABLE) from None
    if len(data) > MAX_POLICY_BYTES or not text.strip():
        raise PolicyError(POLICY_UNAVAILABLE)
    return text.replace("\r\n", "\n")


def tonalitaet_text(directory: Path) -> str:
    return read_policy(directory, TONALITAET_FILE)


def rabatte_text(directory: Path) -> str:
    """Vorspann, Eskalationsregeln, Trenner, Rabattregeln: beide Dateien müssen lesbar sein."""
    escalation = read_policy(directory, ESKALATION_FILE)
    discounts = read_policy(directory, RABATTE_FILE)
    return RABATTE_PREAMBLE + escalation + RABATTE_SEPARATOR + discounts


def _signature_sections(text: str) -> dict[str, str] | None:
    """Die Abschnitte der Signatur-Datei nach ihren Überschriften der zweiten Ebene (## Name).

    None, wenn eine Überschrift doppelt vorkommt: Abschnitte werden nicht zusammengeführt.
    """
    sections: dict[str, list[str]] = {}
    current: list[str] | None = None
    for line in text.split("\n"):
        if line.startswith("## "):
            name = line[3:].strip()
            if name in sections:
                return None
            current = sections.setdefault(name, [])
        if current is not None:
            current.append(line)
    return {name: "\n".join(lines).strip("\n") for name, lines in sections.items()}


def _complete_sections(text: str) -> dict[str, str] | None:
    """Die Abschnitte, wenn alle Pflichtabschnitte genau einmal vorkommen; sonst None."""
    sections = _signature_sections(text)
    if sections is None or not all(name in sections for name in _SIGNATURE_REQUIRED):
        return None
    return sections


def signature_section(directory: Path, sprache: str) -> str:
    """Der Abschnitt der Sprache (de oder en) und der Abschnitt Regeln.

    Die Datei braucht alle drei Abschnitte, je genau einmal; jede andere Sprache ist ein Fehler.
    """
    if sprache not in _SIGNATURE_HEADINGS:
        raise PolicyError(POLICY_UNAVAILABLE)
    sections = _complete_sections(read_policy(directory, SIGNATUR_FILE))
    if sections is None:
        raise PolicyError(POLICY_UNAVAILABLE)
    return sections[_SIGNATURE_HEADINGS[sprache]] + "\n\n" + sections["Regeln"]


def verify_policies(directory: Path) -> list[str]:
    """Kurzbezeichnungen der Probleme (leer, wenn alles in Ordnung ist), ohne Pfad, in fester Reihenfolge.

    richtlinie-fehlt:<name>: Datei fehlt oder ist nicht lesbar; richtlinie-abschnitt:signatur: einer der Abschnitte
    Deutsch, Englisch oder Regeln fehlt, oder eine Überschrift der zweiten Ebene kommt doppelt vor.
    """
    found: list[str] = []
    for name, filename in (
        ("tonalitaet", TONALITAET_FILE),
        ("eskalation", ESKALATION_FILE),
        ("rabatte", RABATTE_FILE),
        ("signatur", SIGNATUR_FILE),
    ):
        try:
            text = read_policy(directory, filename)
        except PolicyError:
            found.append(f"richtlinie-fehlt:{name}")
            continue
        if name == "signatur" and _complete_sections(text) is None:
            found.append("richtlinie-abschnitt:signatur")
    return found
