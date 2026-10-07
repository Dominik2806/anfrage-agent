"""Erzeugt data/richtlinien/rabatte.md aus den Rabattregeln (F05, Quelle: docs/DATENMODELL.md).

render_rabatte macht aus den geladenen Regeln deterministisch einen Markdown-Text. write_rabatte
schreibt ihn atomar: erst in eine temporäre Datei im selben Ordner, dann os.replace. Bei einem
Fehler bleibt weder eine halbe noch eine temporäre Datei zurück, die alte Datei bleibt unverändert.
"""

import os
import tempfile
from decimal import Decimal
from pathlib import Path

from .loader import SeedData

GENERATED_LINE = (
    "<!-- GENERIERT aus data/stammdaten/discount_rules.json durch python -m db.seed. "
    "Nicht von Hand ändern. -->"
)


def _percent(value: Decimal) -> str:
    return f"{value:.2f}".replace(".", ",")


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def render_rabatte(data: SeedData) -> str:
    """Markdown mit allen Rabattregeln, sortiert nach Kundenstatus, Kategorie und Menge."""
    rules = sorted(
        data.discount_rules,
        key=lambda rule: (
            rule.customer_status or "",
            rule.product_category or "",
            rule.min_quantity,
        ),
    )
    lines = [
        GENERATED_LINE,
        "",
        "# Rabattregeln (intern)",
        "",
        "Diese Werte sind interne Obergrenzen und ein Maßstab für den Innendienst. Sie sind keine "
        "Zusage. Der Agent nennt sie nie in einem Antwortentwurf und sagt nie einen Rabatt zu, "
        "auch nicht innerhalb der Regel (siehe eskalation.md und tonalitaet.md).",
        "",
        "Alle Werte sind fiktive Platzhalter und von der Fachseite nicht geprüft.",
        "",
        "| Kundenstatus | Kategorie | ab Menge | Obergrenze (Prozent) | Beschreibung |",
        "|---|---|---|---|---|",
    ]
    for rule in rules:
        cells = [
            rule.customer_status or "alle",
            rule.product_category or "alle",
            str(rule.min_quantity),
            _percent(rule.max_discount_percent),
            _cell(rule.description),
        ]
        lines.append("| " + " | ".join(cells) + " |")
    return "\n".join(lines) + "\n"


def write_rabatte(path: Path, text: str) -> None:
    """Schreibt text nach path, atomar (temporäre Datei im selben Ordner, dann os.replace)."""
    path = Path(path)
    handle, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=f".{path.name}.", suffix=".tmp")
    tmp_path = Path(tmp_name)
    try:
        with os.fdopen(handle, "w", encoding="utf-8", newline="\n") as file:
            file.write(text)
            file.flush()
            os.fsync(file.fileno())
        os.replace(tmp_path, path)
    except BaseException:
        tmp_path.unlink(missing_ok=True)
        raise
