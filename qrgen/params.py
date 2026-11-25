from __future__ import annotations

import math
import re
from dataclasses import dataclass

from qrgen.errors import InputError
from qrgen.fonts import available_fonts, default_font_id

LAYOUTS = ("qr-left", "qr-right", "qr-top", "qr-bottom")
ALIGNS_H = ("left", "center", "right")
ALIGNS_V = ("top", "middle", "bottom")
EC_LEVELS = ("L", "M", "Q", "H")

_COLOR = re.compile(r"#[0-9A-Fa-f]{6}")


@dataclass
class Params:
    url: str
    text: str
    font_id: str
    bold: bool
    text_height: float
    text_layer: float
    text_align_h: str
    text_align_v: str
    fit_text: bool
    qr_size: float
    qr_layer: float
    qr_border: int
    qr_ec: str
    base_w: float
    base_d: float
    base_h: float
    corner_radius: float
    qr_x: float
    qr_y: float
    text_x: float
    text_y: float
    gap: float
    layout: str
    color_base: str
    color_qr: str
    color_text: str
    same_ink: bool
    svg: str
    svg_width: float
    svg_x: float
    svg_y: float
    svg_layer: float
    color_svg: str
    svg_same: bool


DEFAULTS = {
    "url": "https://github.com/Wilkuuu/qrgen-3MF",
    "text": "QRGEN\nlink w kodzie",
    "font_id": "segoe",
    "bold": False,
    "text_height": 6.5,
    "text_layer": 0.8,
    "text_align_h": "center",
    "text_align_v": "middle",
    "fit_text": True,
    "qr_size": 26.0,
    "qr_layer": 0.8,
    "qr_border": 2,
    "qr_ec": "M",
    "base_w": 85.0,
    "base_d": 36.0,
    "base_h": 2.0,
    "corner_radius": 0.0,
    "qr_x": 0.0,
    "qr_y": 0.0,
    "text_x": 0.0,
    "text_y": 0.0,
    "gap": 3.0,
    "layout": "qr-left",
    "color_base": "#1B1B1B",
    "color_qr": "#F4F4F4",
    "color_text": "#F4F4F4",
    "same_ink": True,
    "svg": "",
    "svg_width": 20.0,
    "svg_x": 3.0,
    "svg_y": 3.0,
    "svg_layer": 0.8,
    "color_svg": "#3D7EFF",
    "svg_same": True,
}


def _bool(value, name: str) -> bool:
    if isinstance(value, bool):
        return value
    if isinstance(value, (int, float)) and value in (0, 1):
        return bool(value)
    if isinstance(value, str) and value.lower() in {"1", "true", "yes", "on"}:
        return True
    if isinstance(value, str) and value.lower() in {"0", "false", "no", "off", ""}:
        return False
    raise InputError(f"Pole „{name}” ma nieprawidłową wartość.")


def _float(value, name: str, lo: float, hi: float) -> float:
    try:
        number = float(value)
    except (TypeError, ValueError):
        raise InputError(f"Pole „{name}” musi być liczbą.") from None
    if not math.isfinite(number) or number < lo or number > hi:
        raise InputError(f"Pole „{name}” musi być między {lo:g} a {hi:g}.")
    return number


def _int(value, name: str, lo: int, hi: int) -> int:
    number = _float(value, name, lo, hi)
    if abs(number - round(number)) > 1e-6:
        raise InputError(f"Pole „{name}” musi być liczbą całkowitą.")
    return int(round(number))


def _color(value, name: str) -> str:
    if not isinstance(value, str) or not _COLOR.fullmatch(value.strip()):
        raise InputError(f"Kolor „{name}” wpisz jako #RRGGBB.")
    return value.strip().upper()


def _choice(value, name: str, allowed: tuple[str, ...]) -> str:
    if not isinstance(value, str) or value not in allowed:
        raise InputError(f"Pole „{name}” ma niedozwoloną wartość.")
    return value


def _font_id(value) -> str:
    requested = str(value or "")
    known = {choice.id for choice in available_fonts()}
    if requested in known:
        return requested
    return default_font_id()


def parse_params(data: dict) -> Params:
    if not isinstance(data, dict):
        raise InputError("Brak danych formularza.")
    merged = dict(DEFAULTS)
    for key, value in data.items():
        if key in merged and value is not None:
            merged[key] = value

    url = str(merged["url"] or "").strip()
    text = str(merged["text"] or "").replace("\r\n", "\n").replace("\r", "\n")
    text = text.replace("\t", "    ")
    svg = str(merged["svg"] or "")
    if len(url) > 1500:
        raise InputError("Link jest za długi (maks. 1500 znaków).")
    if len(text) > 400:
        raise InputError("Tekst jest za długi (maks. 400 znaków).")
    if len(svg) > 500_000:
        raise InputError("Plik SVG jest za duży (maks. 500 KB).")
    has_svg = "<svg" in svg.lower()
    if not url and not text.strip() and not has_svg:
        raise InputError("Wpisz link do kodu QR, tekst albo wczytaj SVG.")

    same_ink = _bool(merged["same_ink"], "ten sam kolor napisu")
    svg_same = _bool(merged["svg_same"], "kolor SVG")
    color_qr = _color(merged["color_qr"], "QR")
    color_text = color_qr if same_ink else _color(merged["color_text"], "tekst")
    if svg_same:
        color_svg = color_text if text.strip() else color_qr
    else:
        color_svg = _color(merged["color_svg"], "SVG")

    params = Params(
        url=url,
        text=text,
        font_id=_font_id(merged["font_id"]),
        bold=_bool(merged["bold"], "pogrubienie"),
        text_height=_float(merged["text_height"], "wysokość liter", 1.0, 80.0),
        text_layer=_float(merged["text_layer"], "grubość tekstu", 0.2, 6.0),
        text_align_h=_choice(str(merged["text_align_h"]), "wyrównanie poziome", ALIGNS_H),
        text_align_v=_choice(str(merged["text_align_v"]), "wyrównanie pionowe", ALIGNS_V),
        fit_text=_bool(merged["fit_text"], "dopasuj tekst"),
        qr_size=_float(merged["qr_size"], "rozmiar QR", 8.0, 320.0),
        qr_layer=_float(merged["qr_layer"], "grubość QR", 0.2, 6.0),
        qr_border=_int(merged["qr_border"], "margines QR", 0, 8),
        qr_ec=_choice(str(merged["qr_ec"]).upper(), "korekcja QR", EC_LEVELS),
        base_w=_float(merged["base_w"], "szerokość podstawy", 10.0, 350.0),
        base_d=_float(merged["base_d"], "głębokość podstawy", 10.0, 350.0),
        base_h=_float(merged["base_h"], "grubość podstawy", 0.4, 30.0),
        corner_radius=_float(merged["corner_radius"], "zaokrąglenie", 0.0, 160.0),
        qr_x=_float(merged["qr_x"], "położenie X QR", -80.0, 400.0),
        qr_y=_float(merged["qr_y"], "położenie Y QR", -80.0, 400.0),
        text_x=_float(merged["text_x"], "położenie X tekstu", -80.0, 400.0),
        text_y=_float(merged["text_y"], "położenie Y tekstu", -80.0, 400.0),
        gap=_float(merged["gap"], "odstęp", 0.0, 40.0),
        layout=_choice(str(merged["layout"]), "ułożenie", LAYOUTS),
        color_base=_color(merged["color_base"], "podstawa"),
        color_qr=color_qr,
        color_text=color_text,
        same_ink=same_ink,
        svg=svg if has_svg else "",
        svg_width=_float(merged["svg_width"], "szerokość SVG", 2.0, 320.0),
        svg_x=_float(merged["svg_x"], "pozycja X SVG", -50.0, 400.0),
        svg_y=_float(merged["svg_y"], "pozycja Y SVG", -50.0, 400.0),
        svg_layer=_float(merged["svg_layer"], "grubość SVG", 0.2, 6.0),
        color_svg=color_svg,
        svg_same=svg_same,
    )
    return params
