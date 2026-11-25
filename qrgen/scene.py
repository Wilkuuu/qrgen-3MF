from __future__ import annotations

from dataclasses import dataclass, field

import qrcode
from shapely.affinity import scale as scale_geom
from shapely.affinity import translate
from shapely.geometry import Polygon
from shapely.ops import unary_union

from qrgen.errors import InputError
from qrgen.fonts import resolve_font
from qrgen.mesh import plate_polygon
from qrgen.params import Params
from qrgen.svg2d import svg_polygons
from qrgen.text2d import render_text

EC = {
    "L": qrcode.constants.ERROR_CORRECT_L,
    "M": qrcode.constants.ERROR_CORRECT_M,
    "Q": qrcode.constants.ERROR_CORRECT_Q,
    "H": qrcode.constants.ERROR_CORRECT_H,
}


@dataclass
class Box:
    x: float
    y: float
    w: float
    h: float


@dataclass
class Scene:
    params: Params
    qr_rects: list[tuple[float, float, float, float]] = field(default_factory=list)
    text_polygons: list[Polygon] = field(default_factory=list)
    svg_polygons: list[Polygon] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    module_mm: float = 0.0
    modules: int = 0
    text_height_used: float = 0.0
    font_label: str = ""

    @property
    def filament_colors(self) -> list[str]:
        colors: list[str] = []
        for _name, color, used in self._parts():
            if used and color not in colors:
                colors.append(color)
        return colors

    def _parts(self) -> list[tuple[str, str, bool]]:
        params = self.params
        return [
            ("Podstawa", params.color_base, True),
            ("QR", params.color_qr, bool(self.qr_rects)),
            ("Tekst", params.color_text, bool(self.text_polygons)),
            ("SVG", params.color_svg, bool(self.svg_polygons)),
        ]

    def _uses(self, color: str) -> bool:
        return any(used and part_color == color for _name, part_color, used in self._parts())

    def color_slot(self, color: str) -> int:
        return self.filament_colors.index(color) + 1

    def info(self) -> dict:
        params = self.params
        lines = [
            f"Podstawa {params.base_w:g} × {params.base_d:g} × {params.base_h:g} mm",
        ]
        if self.qr_rects:
            left = min(x for x, _y, _w, _h in self.qr_rects)
            right = max(x + w for x, _y, w, _h in self.qr_rects)
            bottom = min(y for _x, y, _w, _h in self.qr_rects)
            top = max(y + h for _x, y, _w, h in self.qr_rects)
            qr_mm = max(right - left, top - bottom)
            lines.append(
                f"QR {qr_mm:.1f} mm, {self.modules}×{self.modules} modułów, moduł {self.module_mm:.2f} mm"
            )
        if self.text_polygons:
            lines.append(f"Tekst {self.text_height_used:.2f} mm, warstwa {params.text_layer:g} mm")
        if self.svg_polygons:
            lines.append(f"SVG {params.svg_width:g} mm, warstwa {params.svg_layer:g} mm")
        lines.append(f"nadruk od {params.base_h:g} mm")
        lines.append(f"{len(self.filament_colors)} {'filament' if len(self.filament_colors) == 1 else 'filamenty' if len(self.filament_colors) < 5 else 'filamentów'}")
        legend = []
        grouped: dict[str, list[str]] = {}
        for name, color, used in self._parts():
            if used:
                grouped.setdefault(color, []).append(name)
        for color, names in grouped.items():
            legend.append({"color": color, "label": " + ".join(names)})
        return {
            "summary": " · ".join(lines),
            "warnings": self.warnings,
            "filaments": len(self.filament_colors),
            "colors": self.filament_colors,
            "legend": legend,
            "module_mm": round(self.module_mm, 3),
            "text_height_mm": round(self.text_height_used, 3),
            "relief_z": params.base_h,
        }

    def to_svg(self) -> str:
        params = self.params
        pad = max(3.0, min(params.base_w, params.base_d) * 0.06)
        view = f"{-pad:.2f} {-pad:.2f} {params.base_w + 2 * pad:.2f} {params.base_d + 2 * pad:.2f}"
        stroke = _contrast(params.color_base)
        parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{view}" preserveAspectRatio="xMidYMid meet" role="img">',
            f'<path fill="{params.color_base}" stroke="{stroke}" stroke-width="{_n(max(params.base_w, params.base_d) * 0.004)}" d="{_svg_path(plate_polygon(params.base_w, params.base_d, params.corner_radius), params.base_d)}"/>',
        ]
        if params.margin > 0.2:
            inset_w = params.base_w - 2 * params.margin
            inset_h = params.base_d - 2 * params.margin
            parts.append(
                f'<rect x="{_n(params.margin)}" y="{_n(params.margin)}" width="{_n(inset_w)}" height="{_n(inset_h)}" fill="none" stroke="{stroke}" stroke-opacity="0.35" stroke-width="{_n(max(params.base_w, params.base_d) * 0.002)}" stroke-dasharray="{_n(max(params.base_w, params.base_d) * 0.012)} {_n(max(params.base_w, params.base_d) * 0.008)}"/>'
            )
        for x, y, w, h in self.qr_rects:
            svg_y = params.base_d - (y + h)
            parts.append(
                f'<rect x="{_n(x)}" y="{_n(svg_y)}" width="{_n(w)}" height="{_n(h)}" fill="{params.color_qr}"/>'
            )
        if self.text_polygons:
            paths = [_svg_path(polygon, params.base_d) for polygon in self.text_polygons]
            parts.append(
                f'<path fill="{params.color_text}" fill-rule="evenodd" d="{" ".join(paths)}"/>'
            )
        if self.svg_polygons:
            paths = [_svg_path(polygon, params.base_d) for polygon in self.svg_polygons]
            parts.append(
                f'<path fill="{params.color_svg}" fill-rule="evenodd" d="{" ".join(paths)}"/>'
            )
        parts.append("</svg>")
        return "".join(parts)


def build_scene(params: Params) -> Scene:
    scene = Scene(params=params)
    has_qr = bool(params.url)
    has_text = bool(params.text.strip())
    if has_qr or has_text:
        qr_box, text_box, layout_warning = _content_boxes(params, has_qr, has_text)
        if layout_warning:
            scene.warnings.append(layout_warning)
    else:
        qr_box, text_box = None, None

    if has_qr and qr_box is not None:
        matrix = _qr_matrix(params)
        count = len(matrix)
        scene.modules = count
        scene.module_mm = qr_box.w / count
        if scene.module_mm < 0.45:
            scene.warnings.append(
                f"Moduł QR ma tylko {scene.module_mm:.2f} mm. Na dyszy 0.4 mm kod może się zlewać — powiększ QR albo skróć link."
            )
        elif scene.module_mm < 0.6:
            scene.warnings.append(
                f"Moduł QR ma {scene.module_mm:.2f} mm. Da się drukować, ale bezpieczniej zejść powyżej 0.6 mm."
            )
        scene.qr_rects = _module_rects(matrix, qr_box)
        if not any(row for row in matrix):
            raise InputError("Kod QR wyszedł pusty.")

    if has_text and text_box is not None:
        font_path, used_bold = resolve_font(params.font_id, params.bold)
        if params.bold and not used_bold:
            scene.warnings.append("Ten krój nie ma osobnego pogrubienia. Użyty został zwykły.")
        scene.font_label = font_path.name
        polygons, text_warnings = render_text(params.text, str(font_path), params.text_height, params.text_align_h)
        scene.warnings.extend(text_warnings)
        if not polygons:
            raise InputError("Tekst nie zawiera znaków, które da się wytłoczyć.")
        placed, used_height, fit_warning = _place_text(polygons, text_box, params)
        scene.text_polygons = placed
        scene.text_height_used = used_height
        if fit_warning:
            scene.warnings.append(fit_warning)

    if params.svg:
        scene.svg_polygons = svg_polygons(params.svg, params.svg_width, params.svg_x, params.svg_y)
        bounds = unary_union(scene.svg_polygons).bounds
        if bounds[0] < -0.2 or bounds[1] < -0.2 or bounds[2] > params.base_w + 0.2 or bounds[3] > params.base_d + 0.2:
            scene.warnings.append("SVG wychodzi poza podstawę. Przesuń je albo zmniejsz szerokość.")

    if params.corner_radius > 0:
        plate = plate_polygon(params.base_w, params.base_d, params.corner_radius)
        pieces = list(scene.text_polygons) + list(scene.svg_polygons)
        pieces.extend(_rect_polygon(*rect) for rect in scene.qr_rects)
        if pieces:
            outside = unary_union(pieces).difference(plate.buffer(0.2))
            if not outside.is_empty and outside.area > 0.4:
                scene.warnings.append("Część nadruku wychodzi poza zaokrąglone narożniki.")

    if params.url and not params.url.lower().startswith(("http://", "https://")):
        scene.warnings.append("Link nie zaczyna się od http:// ani https://. QR zakoduje go tak, jak jest wpisany.")
    return scene


def _rect_polygon(x: float, y: float, width: float, height: float) -> Polygon:
    return Polygon([(x, y), (x + width, y), (x + width, y + height), (x, y + height)])


def _qr_matrix(params: Params) -> list[list[bool]]:
    code = qrcode.QRCode(error_correction=EC[params.qr_ec], border=params.qr_border, box_size=1)
    code.add_data(params.url)
    code.make(fit=True)
    return code.get_matrix()


def _module_rects(matrix: list[list[bool]], box: Box) -> list[tuple[float, float, float, float]]:
    count = len(matrix)
    rects = []
    for row, cells in enumerate(matrix):
        column = 0
        width = len(cells)
        while column < width:
            if not cells[column]:
                column += 1
                continue
            end = column + 1
            while end < width and cells[end]:
                end += 1
            x0 = box.x + box.w * column / count
            x1 = box.x + box.w * end / count
            y1 = box.y + box.h * (count - row) / count
            y0 = box.y + box.h * (count - row - 1) / count
            rects.append((x0, y0, x1 - x0, y1 - y0))
            column = end
    return rects


def _content_boxes(params: Params, has_qr: bool, has_text: bool) -> tuple[Box | None, Box | None, str | None]:
    left = params.margin
    bottom = params.margin
    inner_w = params.base_w - 2 * params.margin
    inner_h = params.base_d - 2 * params.margin
    if inner_w <= 1 or inner_h <= 1:
        raise InputError("Po odjęciu marginesu nie zostaje miejsce na treść.")

    if has_qr and not has_text:
        size, warning = _limit_qr(params.qr_size, min(inner_w, inner_h), "na podstawę", alone=True)
        return Box(left + (inner_w - size) / 2, bottom + (inner_h - size) / 2, size, size), None, warning

    if has_text and not has_qr:
        return None, Box(left, bottom, inner_w, inner_h), None

    gap = params.gap
    if params.layout in {"qr-left", "qr-right"}:
        size, warning = _limit_qr(params.qr_size, _stack_limit(inner_w, inner_h, gap), "obok tekstu")
        qr_y = bottom + (inner_h - size) / 2
        text_w = inner_w - size - gap
        if params.layout == "qr-left":
            return Box(left, qr_y, size, size), Box(left + size + gap, bottom, text_w, inner_h), warning
        return Box(left + inner_w - size, qr_y, size, size), Box(left, bottom, text_w, inner_h), warning

    where = "nad tekstem" if params.layout == "qr-top" else "pod tekstem"
    size, warning = _limit_qr(params.qr_size, _stack_limit(inner_h, inner_w, gap), where)
    qr_x = left + (inner_w - size) / 2
    text_h = inner_h - size - gap
    if params.layout == "qr-top":
        return Box(qr_x, bottom + text_h + gap, size, size), Box(left, bottom, inner_w, text_h), warning
    return Box(qr_x, bottom, size, size), Box(left, bottom + size + gap, inner_w, text_h), warning


def _stack_limit(along: float, cross: float, gap: float) -> float:
    """Największy QR, który zostawia na tej osi miejsce na tekst."""
    text_keep = min(max(6.0, along * 0.28), along * 0.55)
    return min(cross, along - gap - text_keep)


def _limit_qr(requested: float, limit: float, where: str, alone: bool = False) -> tuple[float, str | None]:
    if limit < 8:
        if alone:
            raise InputError("QR nie mieści się na podstawie. Zwiększ płytkę albo zmniejsz margines.")
        raise InputError("Na tej podstawie nie mieści się QR razem z tekstem. Powiększ płytkę albo zmniejsz margines i odstęp.")
    if requested > limit + 0.05:
        return limit, f"QR zmniejszony do {limit:.1f} mm, żeby zmieścił się {where}."
    return requested, None


def _place_text(polygons: list[Polygon], box: Box, params: Params):
    bounds = unary_union(polygons).bounds
    min_x, min_y, max_x, max_y = bounds
    width = max_x - min_x
    height = max_y - min_y
    if width <= 0 or height <= 0:
        raise InputError("Tekst ma zerowy rozmiar.")
    factor = min(box.w / width, box.h / height)
    warning = None
    used_height = params.text_height
    if factor < 0.995:
        if not params.fit_text:
            raise InputError(
                "Tekst nie mieści się w polu. Włącz dopasowanie, zmniejsz litery albo powiększ podstawę."
            )
        polygons = [scale_geom(polygon, factor, factor, origin=(min_x, min_y)) for polygon in polygons]
        used_height = params.text_height * factor
        warning = f"Litery zmniejszone do {used_height:.2f} mm, żeby weszły w pole."
        if factor < 0.45:
            warning += " Tekst jest bardzo mały — rozważ większą płytkę."
        bounds = unary_union(polygons).bounds
        min_x, min_y, max_x, max_y = bounds
        width = max_x - min_x
        height = max_y - min_y

    if params.text_align_h == "left":
        target_x = box.x
    elif params.text_align_h == "right":
        target_x = box.x + box.w - width
    else:
        target_x = box.x + (box.w - width) / 2
    if params.text_align_v == "bottom":
        target_y = box.y
    elif params.text_align_v == "top":
        target_y = box.y + box.h - height
    else:
        target_y = box.y + (box.h - height) / 2

    dx = target_x - min_x
    dy = target_y - min_y
    placed = [translate(polygon, dx, dy) for polygon in polygons]
    return placed, used_height, warning


def _svg_path(polygon: Polygon, depth: float) -> str:
    commands = []
    for ring in (polygon.exterior, *polygon.interiors):
        coords = list(ring.coords)
        if len(coords) < 2:
            continue
        x0, y0 = coords[0]
        commands.append(f"M{_n(x0)} {_n(depth - y0)}")
        for x, y in coords[1:]:
            commands.append(f"L{_n(x)} {_n(depth - y)}")
        commands.append("Z")
    return " ".join(commands)


def _n(value: float) -> str:
    return f"{value:.3f}"


def _contrast(color: str) -> str:
    red = int(color[1:3], 16)
    green = int(color[3:5], 16)
    blue = int(color[5:7], 16)
    luminance = 0.2126 * red + 0.7152 * green + 0.0722 * blue
    return "#F3EFE4" if luminance < 150 else "#1A1A1A"
