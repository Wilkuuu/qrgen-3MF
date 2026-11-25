from __future__ import annotations

import math
import re
from xml.etree import ElementTree as ET

from shapely.affinity import scale as scale_geom
from shapely.affinity import translate
from shapely.geometry import LineString, Polygon
from shapely.ops import unary_union

from qrgen.errors import InputError
from qrgen.text2d import contours_to_polygons

_NUM = re.compile(r"[+-]?(?:\d+\.?\d*|\.\d+)(?:[eE][+-]?\d+)?")
_TRANSFORM = re.compile(r"(matrix|translate|scale|rotate|skewX|skewY)\s*\(([^)]*)\)")
_ENTITY_REF = re.compile(r"&([A-Za-z_:][\w:.-]*);")
_ENTITY_NAME = re.compile(r"([A-Za-z_:][\w:.-]*)\s+(.*)\Z", re.DOTALL)
_BUILTIN_ENTITIES = {"lt", "gt", "amp", "quot", "apos"}


def svg_polygons(source: str, width_mm: float, origin_x: float, origin_y: float) -> list[Polygon]:
    text = source.strip()
    if not text:
        return []
    if len(text) > 500_000:
        raise InputError("Plik SVG jest za duży (maks. 500 KB).")
    text = _prepare_svg(text)
    try:
        root = ET.fromstring(text)
    except ET.ParseError as error:
        raise InputError("Nie da się odczytać pliku SVG.") from error

    shapes: list[Polygon] = []
    _walk(root, _identity(), {"fill": "#000000", "fill-rule": "nonzero"}, shapes)
    if not shapes:
        raise InputError("W SVG nie ma kształtów do wytłoczenia. Tekst w SVG trzeba wcześniej zamienić na krzywe.")

    flipped = [scale_geom(polygon, 1.0, -1.0, origin=(0.0, 0.0)) for polygon in shapes]
    bounds = unary_union(flipped).bounds
    min_x, min_y, max_x, max_y = bounds
    graphic_w = max_x - min_x
    graphic_h = max_y - min_y
    if graphic_w <= 1e-6 or graphic_h <= 1e-6:
        raise InputError("Grafika SVG ma zerowy rozmiar.")
    factor = width_mm / graphic_w
    placed = []
    for polygon in flipped:
        scaled = scale_geom(polygon, factor, factor, origin=(min_x, min_y))
        placed.append(translate(scaled, origin_x - min_x, origin_y - min_y))
    return placed


def _prepare_svg(text: str) -> str:
    """Rozwija encje wewnętrzne i usuwa DOCTYPE. Encji zewnętrznych nie pobiera."""
    lower = text.lower()
    if "<!doctype" not in lower and "<!entity" not in lower:
        return text
    entities: dict[str, str] = {}
    external: set[str] = set()
    pieces: list[str] = []
    index = 0
    while index < len(text):
        doctype_at = lower.find("<!doctype", index)
        entity_at = lower.find("<!entity", index)
        starts = [pos for pos in (doctype_at, entity_at) if pos != -1]
        if not starts:
            pieces.append(text[index:])
            break
        start = min(starts)
        pieces.append(text[index:start])
        if doctype_at != -1 and start == doctype_at:
            end, subset = _read_doctype(text, start)
            _collect_entities(subset, entities, external)
        else:
            end = _read_markup(text, start)
            _collect_entities(text[start:end], entities, external)
        index = end
    body = "".join(pieces)
    used = set(_ENTITY_REF.findall(body)) - _BUILTIN_ENTITIES
    if used & external:
        raise InputError("SVG odwołuje się do zewnętrznej encji. Zapisz plik bez DOCTYPE i bez encji SYSTEM albo PUBLIC.")
    return _expand_entities(body, entities)


def _read_doctype(text: str, start: int) -> tuple[int, str]:
    quote = ""
    depth = 0
    subset_start = -1
    subset_end = -1
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
        elif char == "[":
            depth += 1
            if depth == 1:
                subset_start = index + 1
        elif char == "]":
            if depth == 1:
                subset_end = index
            depth = max(0, depth - 1)
        elif char == ">" and depth == 0:
            subset = text[subset_start:subset_end] if subset_start >= 0 and subset_end >= subset_start else ""
            return index + 1, subset
    raise InputError("Nie da się odczytać pliku SVG.")


def _read_markup(text: str, start: int) -> int:
    quote = ""
    for index in range(start, len(text)):
        char = text[index]
        if quote:
            if char == quote:
                quote = ""
            continue
        if char in "\"'":
            quote = char
        elif char == ">":
            return index + 1
    raise InputError("Nie da się odczytać pliku SVG.")


def _collect_entities(subset: str, entities: dict[str, str], external: set[str]) -> None:
    lower = subset.lower()
    index = 0
    while True:
        start = lower.find("<!entity", index)
        if start == -1:
            return
        end = _read_markup(subset, start)
        body = subset[start + len("<!ENTITY") : end - 1].strip()
        index = end
        if not body or body.startswith("%"):
            continue
        matched = _ENTITY_NAME.match(body)
        if not matched:
            continue
        name, rest = matched.group(1), matched.group(2).strip()
        kind = rest[:6].upper()
        if kind.startswith("SYSTEM") or kind.startswith("PUBLIC"):
            external.add(name)
            continue
        if rest[:1] in "\"'":
            quote = rest[0]
            end_quote = rest.find(quote, 1)
            if end_quote != -1:
                entities[name] = rest[1:end_quote]


def _expand_entities(text: str, entities: dict[str, str]) -> str:
    if not entities:
        return text
    pattern = re.compile("&(" + "|".join(re.escape(name) for name in entities) + ");")
    for _ in range(16):
        expanded = pattern.sub(lambda match: entities.get(match.group(1), match.group(0)), text)
        if expanded == text:
            return text
        if len(expanded) > 500_000:
            raise InputError("Plik SVG jest za duży (maks. 500 KB).")
        text = expanded
    raise InputError("Definicje encji w SVG zapętlają się.")


def _walk(element, matrix, inherited: dict, output: list[Polygon]) -> None:
    style = dict(inherited)
    for key in ("fill", "stroke", "stroke-width", "display", "visibility", "fill-rule", "opacity"):
        value = element.get(key)
        if value:
            style[key] = value.strip()
    if element.get("style"):
        for part in element.get("style", "").split(";"):
            if ":" not in part:
                continue
            key, value = part.split(":", 1)
            style[key.strip().lower()] = value.strip()
    if style.get("display") == "none" or style.get("visibility") == "hidden":
        return
    if style.get("opacity") in {"0", "0.0"}:
        return

    local = _multiply(matrix, _parse_transform(element.get("transform", "")))
    name = element.tag.split("}")[-1] if isinstance(element.tag, str) else ""
    contours = _shape_contours(name, element, local)
    if contours:
        filled = style.get("fill", "#000") not in {"none", "transparent"}
        evenodd = style.get("fill-rule", "nonzero") == "evenodd"
        if filled:
            output.extend(contours_to_polygons(contours, evenodd=evenodd))
        elif style.get("stroke") not in {None, "", "none", "transparent"}:
            width = _float(style.get("stroke-width"), 1.0)
            for contour in contours:
                if len(contour) < 2:
                    continue
                line = LineString(contour)
                if line.length <= 0:
                    continue
                output.append(line.buffer(max(width, 0.2) / 2, quad_segs=4))
    for child in list(element):
        _walk(child, local, style, output)


def _shape_contours(name: str, element, matrix) -> list[list[tuple[float, float]]]:
    if name == "rect":
        x = _float(element.get("x"), 0.0)
        y = _float(element.get("y"), 0.0)
        width = _float(element.get("width"), 0.0)
        height = _float(element.get("height"), 0.0)
        if width <= 0 or height <= 0:
            return []
        rx = _float(element.get("rx"), 0.0)
        ry = _float(element.get("ry"), rx if rx else 0.0)
        if rx and not element.get("ry"):
            ry = rx
        if ry and not element.get("rx"):
            rx = ry
        return [[_apply(matrix, px, py) for px, py in _rounded_rect(x, y, width, height, rx, ry)]]
    if name == "circle":
        return [_circle(_float(element.get("cx"), 0.0), _float(element.get("cy"), 0.0), _float(element.get("r"), 0.0), matrix)]
    if name == "ellipse":
        return [_ellipse(
            _float(element.get("cx"), 0.0),
            _float(element.get("cy"), 0.0),
            _float(element.get("rx"), 0.0),
            _float(element.get("ry"), 0.0),
            matrix,
        )]
    if name in {"polygon", "polyline"}:
        points = _points_attr(element.get("points", ""))
        if name == "polygon" and len(points) >= 3:
            points = points + [points[0]]
        if len(points) < 2:
            return []
        return [[_apply(matrix, x, y) for x, y in points]]
    if name == "path":
        return _path_contours(element.get("d", ""), matrix)
    return []


def _path_contours(data: str, matrix) -> list[list[tuple[float, float]]]:
    tokens = list(_tokens(data))
    contours: list[list[tuple[float, float]]] = []
    current: list[tuple[float, float]] = []
    cursor = (0.0, 0.0)
    start = (0.0, 0.0)
    command = ""
    index = 0
    previous_cubic: tuple[float, float] | None = None
    previous_quad: tuple[float, float] | None = None

    def take(count: int) -> list[float]:
        nonlocal index
        values = []
        for _ in range(count):
            if index >= len(tokens) or tokens[index][0] != "num":
                raise InputError("Ścieżka SVG jest urwaną albo ma zły format.")
            values.append(tokens[index][1])
            index += 1
        return values

    def line_to(point: tuple[float, float]) -> None:
        nonlocal cursor, previous_cubic, previous_quad
        current.append(_apply(matrix, point[0], point[1]))
        cursor = point
        previous_cubic = None
        previous_quad = None

    def close() -> None:
        nonlocal current, cursor
        if len(current) >= 3:
            contours.append(current)
        current = []
        cursor = start

    while index < len(tokens):
        kind, value = tokens[index]
        if kind == "cmd":
            command = value
            index += 1
        elif not command:
            raise InputError("Ścieżka SVG zaczyna się od liczby, a nie od polecenia.")
        relative = command.islower()
        op = command.upper()
        if op == "Z":
            close()
            command = ""
            continue
        if op == "M":
            if current:
                contours.append(current)
                current = []
            x, y = take(2)
            if relative:
                x += cursor[0]
                y += cursor[1]
            cursor = (x, y)
            start = cursor
            current = [_apply(matrix, x, y)]
            command = "l" if relative else "L"
            previous_cubic = None
            previous_quad = None
        elif op == "L":
            x, y = take(2)
            if relative:
                x += cursor[0]
                y += cursor[1]
            line_to((x, y))
        elif op == "H":
            x = take(1)[0]
            if relative:
                x += cursor[0]
            line_to((x, cursor[1]))
        elif op == "V":
            y = take(1)[0]
            if relative:
                y += cursor[1]
            line_to((cursor[0], y))
        elif op == "C":
            x1, y1, x2, y2, x, y = take(6)
            if relative:
                x1 += cursor[0]
                y1 += cursor[1]
                x2 += cursor[0]
                y2 += cursor[1]
                x += cursor[0]
                y += cursor[1]
            points = _flatten_cubic(cursor, (x1, y1), (x2, y2), (x, y))
            current.extend(_apply(matrix, px, py) for px, py in points)
            previous_cubic = (x2, y2)
            previous_quad = None
            cursor = (x, y)
        elif op == "S":
            x2, y2, x, y = take(4)
            if relative:
                x2 += cursor[0]
                y2 += cursor[1]
                x += cursor[0]
                y += cursor[1]
            if previous_cubic:
                x1 = 2 * cursor[0] - previous_cubic[0]
                y1 = 2 * cursor[1] - previous_cubic[1]
            else:
                x1, y1 = cursor
            points = _flatten_cubic(cursor, (x1, y1), (x2, y2), (x, y))
            current.extend(_apply(matrix, px, py) for px, py in points)
            previous_cubic = (x2, y2)
            previous_quad = None
            cursor = (x, y)
        elif op == "Q":
            x1, y1, x, y = take(4)
            if relative:
                x1 += cursor[0]
                y1 += cursor[1]
                x += cursor[0]
                y += cursor[1]
            points = _flatten_quad(cursor, (x1, y1), (x, y))
            current.extend(_apply(matrix, px, py) for px, py in points)
            previous_quad = (x1, y1)
            previous_cubic = None
            cursor = (x, y)
        elif op == "T":
            x, y = take(2)
            if relative:
                x += cursor[0]
                y += cursor[1]
            if previous_quad:
                x1 = 2 * cursor[0] - previous_quad[0]
                y1 = 2 * cursor[1] - previous_quad[1]
            else:
                x1, y1 = cursor
            points = _flatten_quad(cursor, (x1, y1), (x, y))
            current.extend(_apply(matrix, px, py) for px, py in points)
            previous_quad = (x1, y1)
            previous_cubic = None
            cursor = (x, y)
        elif op == "A":
            rx, ry, phi, large, sweep, x, y = take(7)
            if relative:
                x += cursor[0]
                y += cursor[1]
            points = _arc_points(cursor[0], cursor[1], rx, ry, phi, large, sweep, x, y)
            current.extend(_apply(matrix, px, py) for px, py in points)
            previous_cubic = None
            previous_quad = None
            cursor = (x, y)
        else:
            raise InputError(f"SVG używa nieobsługiwanego polecenia ścieżki „{command}”.")
    if len(current) >= 3:
        contours.append(current)
    return contours


def _tokens(data: str):
    index = 0
    length = len(data)
    while index < length:
        char = data[index]
        if char.isalpha():
            yield ("cmd", char)
            index += 1
            continue
        if char in " ,\t\r\n":
            index += 1
            continue
        match = _NUM.match(data, index)
        if not match:
            index += 1
            continue
        yield ("num", float(match.group()))
        index = match.end()


def _flatten_cubic(p0, p1, p2, p3, depth: int = 0):
    if depth >= 8 or (_distance(p1, p0, p3) <= 0.4 and _distance(p2, p0, p3) <= 0.4):
        return [p3]
    a = _mid(p0, p1)
    b = _mid(p1, p2)
    c = _mid(p2, p3)
    d = _mid(a, b)
    e = _mid(b, c)
    f = _mid(d, e)
    return _flatten_cubic(p0, a, d, f, depth + 1) + _flatten_cubic(f, e, c, p3, depth + 1)


def _flatten_quad(p0, p1, p2, depth: int = 0):
    if depth >= 8 or _distance(p1, p0, p2) <= 0.4:
        return [p2]
    a = _mid(p0, p1)
    b = _mid(p1, p2)
    m = _mid(a, b)
    return _flatten_quad(p0, a, m, depth + 1) + _flatten_quad(m, b, p2, depth + 1)


def _arc_points(x1, y1, rx, ry, phi_deg, large, sweep, x2, y2):
    if math.hypot(x1 - x2, y1 - y2) < 1e-8:
        return []
    rx, ry = abs(rx), abs(ry)
    if rx < 1e-8 or ry < 1e-8:
        return [(x2, y2)]
    phi = math.radians(phi_deg) % (2 * math.pi)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p = cos_p * dx + sin_p * dy
    y1p = -sin_p * dx + cos_p * dy
    lam = (x1p * x1p) / (rx * rx) + (y1p * y1p) / (ry * ry)
    if lam > 1:
        scale = math.sqrt(lam)
        rx *= scale
        ry *= scale
    sign = -1.0 if bool(int(abs(large))) == bool(int(abs(sweep))) else 1.0
    num = rx * rx * ry * ry - rx * rx * y1p * y1p - ry * ry * x1p * x1p
    den = rx * rx * y1p * y1p + ry * ry * x1p * x1p
    coef = sign * math.sqrt(max(0.0, num / den)) if den else 0.0
    cxp = coef * rx * y1p / ry
    cyp = -coef * ry * x1p / rx
    cx = cos_p * cxp - sin_p * cyp + (x1 + x2) / 2
    cy = sin_p * cxp + cos_p * cyp + (y1 + y2) / 2

    def angle(ux, uy, vx, vy) -> float:
        dot = ux * vx + uy * vy
        norm = math.hypot(ux, uy) * math.hypot(vx, vy)
        value = math.acos(max(-1.0, min(1.0, dot / norm))) if norm else 0.0
        if ux * vy - uy * vx < 0:
            value = -value
        return value

    theta = angle(1.0, 0.0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    delta = angle((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    sweeping = bool(int(abs(sweep)))
    if not sweeping and delta > 0:
        delta -= 2 * math.pi
    elif sweeping and delta < 0:
        delta += 2 * math.pi
    steps = max(8, int(abs(delta) * max(rx, ry) / 1.5))
    points = []
    for step in range(1, steps + 1):
        t = theta + delta * step / steps
        points.append((
            cos_p * rx * math.cos(t) - sin_p * ry * math.sin(t) + cx,
            sin_p * rx * math.cos(t) + cos_p * ry * math.sin(t) + cy,
        ))
    return points


def _rounded_rect(x, y, width, height, rx, ry):
    rx = min(abs(rx), width / 2)
    ry = min(abs(ry), height / 2)
    if rx < 1e-6 or ry < 1e-6:
        return [(x, y), (x + width, y), (x + width, y + height), (x, y + height)]
    points = []

    def corner(cx, cy, start, end):
        steps = 8
        for step in range(steps + 1):
            angle = start + (end - start) * step / steps
            points.append((cx + rx * math.cos(angle), cy + ry * math.sin(angle)))

    corner(x + width - rx, y + ry, -math.pi / 2, 0)
    corner(x + width - rx, y + height - ry, 0, math.pi / 2)
    corner(x + rx, y + height - ry, math.pi / 2, math.pi)
    corner(x + rx, y + ry, math.pi, math.pi * 1.5)
    return points


def _circle(cx, cy, radius, matrix):
    if radius <= 0:
        return []
    return _ellipse(cx, cy, radius, radius, matrix)


def _ellipse(cx, cy, rx, ry, matrix):
    if rx <= 0 or ry <= 0:
        return []
    steps = 32
    return [_apply(matrix, cx + rx * math.cos(2 * math.pi * i / steps), cy + ry * math.sin(2 * math.pi * i / steps)) for i in range(steps)]


def _points_attr(text: str) -> list[tuple[float, float]]:
    numbers = [float(value) for value in _NUM.findall(text)]
    return list(zip(numbers[0::2], numbers[1::2]))


def _parse_transform(text: str):
    matrix = _identity()
    for name, args in _TRANSFORM.findall(text or ""):
        values = [float(value) for value in _NUM.findall(args)]
        part = _identity()
        if name == "matrix" and len(values) == 6:
            a, b, c, d, e, f = values
            part = ((a, c, e), (b, d, f), (0.0, 0.0, 1.0))
        elif name == "translate":
            tx = values[0] if values else 0.0
            ty = values[1] if len(values) > 1 else 0.0
            part = ((1.0, 0.0, tx), (0.0, 1.0, ty), (0.0, 0.0, 1.0))
        elif name == "scale":
            sx = values[0] if values else 1.0
            sy = values[1] if len(values) > 1 else sx
            part = ((sx, 0.0, 0.0), (0.0, sy, 0.0), (0.0, 0.0, 1.0))
        elif name == "rotate":
            angle = math.radians(values[0]) if values else 0.0
            cos_a, sin_a = math.cos(angle), math.sin(angle)
            rotation = ((cos_a, -sin_a, 0.0), (sin_a, cos_a, 0.0), (0.0, 0.0, 1.0))
            if len(values) >= 3:
                cx, cy = values[1], values[2]
                part = _multiply(_multiply(_translate(cx, cy), rotation), _translate(-cx, -cy))
            else:
                part = rotation
        elif name == "skewX" and values:
            part = ((1.0, math.tan(math.radians(values[0])), 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))
        elif name == "skewY" and values:
            part = ((1.0, 0.0, 0.0), (math.tan(math.radians(values[0])), 1.0, 0.0), (0.0, 0.0, 1.0))
        matrix = _multiply(matrix, part)
    return matrix


def _identity():
    return ((1.0, 0.0, 0.0), (0.0, 1.0, 0.0), (0.0, 0.0, 1.0))


def _translate(tx: float, ty: float):
    return ((1.0, 0.0, tx), (0.0, 1.0, ty), (0.0, 0.0, 1.0))


def _multiply(a, b):
    return tuple(
        tuple(a[row][0] * b[0][col] + a[row][1] * b[1][col] + a[row][2] * b[2][col] for col in range(3))
        for row in range(3)
    )


def _apply(matrix, x: float, y: float) -> tuple[float, float]:
    return (
        matrix[0][0] * x + matrix[0][1] * y + matrix[0][2],
        matrix[1][0] * x + matrix[1][1] * y + matrix[1][2],
    )


def _float(value, default: float) -> float:
    if value is None or value == "":
        return default
    match = _NUM.match(str(value).strip())
    return float(match.group()) if match else default


def _mid(a, b):
    return ((a[0] + b[0]) / 2, (a[1] + b[1]) / 2)


def _distance(point, start, end) -> float:
    px, py = point
    x1, y1 = start
    x2, y2 = end
    length = math.hypot(x2 - x1, y2 - y1)
    if length < 1e-9:
        return math.hypot(px - x1, py - y1)
    return abs((y2 - y1) * px - (x2 - x1) * py + x2 * y1 - y2 * x1) / length
