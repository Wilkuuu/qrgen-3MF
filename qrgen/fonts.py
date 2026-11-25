from __future__ import annotations

import os
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

from fontTools.ttLib import TTFont

from qrgen.errors import InputError

ROOT = Path(__file__).resolve().parent.parent
CUSTOM_DIR = ROOT / "fonts"
WINDOWS_FONTS = Path(os.environ.get("WINDIR", r"C:\Windows")) / "Fonts"

# (id, etykieta, plik regular, plik bold albo None)
SYSTEM_FACES = (
    ("segoe", "Segoe UI", "segoeui.ttf", "segoeuib.ttf"),
    ("arial", "Arial", "arial.ttf", "arialbd.ttf"),
    ("calibri", "Calibri", "calibri.ttf", "calibrib.ttf"),
    ("verdana", "Verdana", "verdana.ttf", "verdanab.ttf"),
    ("tahoma", "Tahoma", "tahoma.ttf", "tahomabd.ttf"),
    ("georgia", "Georgia", "georgia.ttf", "georgiab.ttf"),
    ("times", "Times New Roman", "times.ttf", "timesbd.ttf"),
    ("consolas", "Consolas", "consola.ttf", "consolab.ttf"),
    ("courier", "Courier New", "cour.ttf", "courbd.ttf"),
    ("comic", "Comic Sans MS", "comic.ttf", "comicbd.ttf"),
    ("impact", "Impact", "impact.ttf", None),
)


@dataclass(frozen=True)
class FontChoice:
    id: str
    label: str
    regular: Path
    bold: Path | None

    def to_json(self) -> dict:
        return {"id": self.id, "label": self.label, "bold": self.bold is not None}


def _existing(path: Path | None) -> Path | None:
    if path is None:
        return None
    return path if path.is_file() else None


def available_fonts() -> list[FontChoice]:
    choices: list[FontChoice] = []
    for font_id, label, regular_name, bold_name in SYSTEM_FACES:
        regular = _existing(WINDOWS_FONTS / regular_name)
        if regular is None:
            continue
        bold = _existing(WINDOWS_FONTS / bold_name) if bold_name else None
        choices.append(FontChoice(font_id, label, regular, bold))

    if CUSTOM_DIR.is_dir():
        for path in sorted(CUSTOM_DIR.iterdir()):
            if path.suffix.lower() not in {".ttf", ".otf"} or not path.is_file():
                continue
            choices.append(
                FontChoice(
                    id=f"file:{path.name}",
                    label=_font_label(path),
                    regular=path,
                    bold=None,
                )
            )
    return choices


def _font_label(path: Path) -> str:
    try:
        font = TTFont(path, fontNumber=0, lazy=True)
        for record in font["name"].names:
            if record.nameID == 4:
                try:
                    return record.toUnicode()
                except Exception:
                    continue
    except Exception:
        pass
    return path.stem


def resolve_font(font_id: str, bold: bool) -> tuple[Path, bool]:
    for choice in available_fonts():
        if choice.id != font_id:
            continue
        if bold and choice.bold is not None:
            return choice.bold, True
        return choice.regular, False
    raise InputError("Wybrany font nie jest dostępny na tym komputerze.")


@lru_cache(maxsize=32)
def open_font(path: str) -> TTFont:
    return TTFont(path, fontNumber=0)
