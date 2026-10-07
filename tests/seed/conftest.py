"""Fixtures für die Tests des Seed-Moduls (F05, ohne Datenbank).

Die Tests laden die echten Stammdaten aus data/stammdaten/ oder eine veränderte Kopie davon
in einem temporären Ordner. Sie brauchen weder eine Datenbank noch Umgebungsvariablen.
Der Repo-Stamm kommt in sys.path, damit "import db.seed" auch mit einem blanken
"pytest tests/seed" funktioniert (bei "python -m pytest" ist er ohnehin enthalten).
"""

import copy
import json
import sys
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

if TYPE_CHECKING:
    from db.seed.loader import SeedData

ROOT = Path(__file__).resolve().parents[2]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

DATA_DIR = ROOT / "data" / "stammdaten"
FILE_NAMES = (
    "products",
    "product_fits",
    "customers",
    "contacts",
    "activities",
    "discount_rules",
)


@pytest.fixture(scope="session")
def data_dir() -> Path:
    return DATA_DIR


@pytest.fixture(scope="session")
def real_data() -> "SeedData":
    """Die echten Stammdaten, einmal je Lauf geladen (die Daten sind unveränderlich)."""
    from db.seed.loader import load_seed_data

    return load_seed_data(DATA_DIR)


@pytest.fixture(scope="session")
def _original_raw() -> dict[str, list[Any]]:
    return {
        name: json.loads((DATA_DIR / f"{name}.json").read_text(encoding="utf-8"))
        for name in FILE_NAMES
    }


@pytest.fixture
def raw(_original_raw: dict[str, list[Any]]) -> dict[str, list[Any]]:
    """Tiefe Kopie der sechs JSON-Listen, die ein Test gezielt verändern darf."""
    return copy.deepcopy(_original_raw)


@pytest.fixture
def write_raw(tmp_path: Path) -> Callable[[dict[str, Any]], Path]:
    """Schreibt die (veränderten) Listen als JSON-Dateien nach tmp_path und gibt den Ordner zurück."""

    def _write(raw_data: dict[str, Any]) -> Path:
        for name, rows in raw_data.items():
            text = json.dumps(rows, ensure_ascii=False, indent=2)
            (tmp_path / f"{name}.json").write_text(text, encoding="utf-8")
        return tmp_path

    return _write


@pytest.fixture
def load_raw(write_raw: Callable[[dict[str, Any]], Path]) -> Callable[[dict[str, Any]], "SeedData"]:
    """Schreibt die veränderten Listen nach tmp_path und lädt sie mit dem Loader."""
    from db.seed.loader import load_seed_data

    def _load(raw_data: dict[str, Any]) -> "SeedData":
        return load_seed_data(write_raw(raw_data))

    return _load
