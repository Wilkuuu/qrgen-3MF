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
FONT_DIRS = (
    WINDOWS_FONTS,
    Path("/usr/share/fonts"),
    Path("/usr/local/share/fonts"),
    Path.home() / ".local/share/fonts",
    Path.home() / ".fonts",
)

# (id, etykieta, pliki regular, pliki bold)
SYSTEM_FACES = (
    ("segoe", "Segoe UI", ("segoeui.ttf",), ("segoeuib.ttf",)),
    ("arial", "Arial", ("arial.ttf",), ("arialbd.ttf",)),
    ("calibri", "Calibri", ("calibri.ttf",), ("calibrib.ttf",)),
    ("verdana", "Verdana", ("verdana.ttf",), ("verdanab.ttf",)),
    ("tahoma", "Tahoma", ("tahoma.ttf",), ("tahomabd.ttf",)),
    ("georgia", "Georgia", ("georgia.ttf",), ("georgiab.ttf",)),
    ("times", "Times New Roman", ("times.ttf",), ("timesbd.ttf",)),
    ("consolas", "Consolas", ("consola.ttf",), ("consolab.ttf",)),
    ("courier", "Courier New", ("cour.ttf",), ("courbd.ttf",)),
    ("comic", "Comic Sans MS", ("comic.ttf",), ("comicbd.ttf",)),
    ("impact", "Impact", ("impact.ttf",), ()),
    ("dejavu-sans", "DejaVu Sans", ("DejaVuSans.ttf",), ("DejaVuSans-Bold.ttf",)),
    ("dejavu-serif", "DejaVu Serif", ("DejaVuSerif.ttf",), ("DejaVuSerif-Bold.ttf",)),
    ("dejavu-mono", "DejaVu Sans Mono", ("DejaVuSansMono.ttf",), ("DejaVuSansMono-Bold.ttf",)),
    ("noto-sans", "Noto Sans", ("NotoSans-Regular.ttf",), ("NotoSans-Bold.ttf",)),
    ("noto-serif", "Noto Serif", ("NotoSerif-Regular.ttf",), ("NotoSerif-Bold.ttf",)),
    ("noto-mono", "Noto Mono", ("NotoMono-Regular.ttf",), ()),
    ("freesans", "FreeSans", ("FreeSans.ttf",), ("FreeSansBold.ttf",)),
    ("freeserif", "FreeSerif", ("FreeSerif.ttf",), ("FreeSerifBold.ttf",)),
    ("freemono", "FreeMono", ("FreeMono.ttf",), ("FreeMonoBold.ttf",)),
    ("nimbus-sans", "Nimbus Sans", ("NimbusSans-Regular.otf",), ("NimbusSans-Bold.otf",)),
    ("nimbus-roman", "Nimbus Roman", ("NimbusRoman-Regular.otf",), ("NimbusRoman-Bold.otf",)),
    ("nimbus-mono", "Nimbus Mono", ("NimbusMonoPS-Regular.otf",), ("NimbusMonoPS-Bold.otf",)),
)

_PREFERRED = ("segoe", "dejavu-sans", "noto-sans", "freesans", "nimbus-sans", "arial")


@dataclass(frozen=True)
class FontChoice:
    id: str
    label: str
    regular: Path
    bold: Path | None

    def to_json(self) -> dict:
        return {"id": self.id, "label": self.label, "bold": self.bold is not None}


@lru_cache(maxsize=1)
def _system_files() -> dict[str, Path]:
    found: dict[str, Path] = {}
    for root in FONT_DIRS:
        if not root.is_dir():
            continue
        try:
            paths = root.iterdir() if root == WINDOWS_FONTS else root.rglob("*")
            for path in paths:
                if path.is_file() and path.suffix.lower() in {".ttf", ".otf", ".ttc"}:
                    found.setdefault(path.name.lower(), path)
        except OSError:
            continue
    return found


def _lookup(names: tuple[str, ...]) -> Path | None:
    files = _system_files()
    for name in names:
        direct = WINDOWS_FONTS / name
        if direct.is_file():
            return direct
        path = files.get(name.lower())
        if path is not None:
            return path
    return None


def available_fonts() -> list[FontChoice]:
    choices: list[FontChoice] = []
    for font_id, label, regular_names, bold_names in SYSTEM_FACES:
        regular = _lookup(regular_names)
        if regular is None:
            continue
        choices.append(FontChoice(font_id, label, regular, _lookup(bold_names)))

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


def default_font_id() -> str:
    choices = available_fonts()
    if not choices:
        raise InputError(
            "Nie znaleziono żadnego fontu. Zainstaluj fonts-dejavu-core albo wrzuć plik .ttf/.otf do folderu fonts."
        )
    known = {choice.id for choice in choices}
    for font_id in _PREFERRED:
        if font_id in known:
            return font_id
    return choices[0].id


def resolve_font(font_id: str, bold: bool) -> tuple[Path, bool]:
    choices = available_fonts()
    for choice in choices:
        if choice.id != font_id:
            continue
        if bold and choice.bold is not None:
            return choice.bold, True
        return choice.regular, False
    if choices and font_id in {"", "segoe"}:
        return resolve_font(default_font_id(), bold)
    if not choices:
        raise InputError(
            "Nie znaleziono żadnego fontu. Zainstaluj fonts-dejavu-core albo wrzuć plik .ttf/.otf do folderu fonts."
        )
    raise InputError("Wybrany font nie jest dostępny na tym komputerze.")


@lru_cache(maxsize=32)
def open_font(path: str) -> TTFont:
    return TTFont(path, fontNumber=0)
