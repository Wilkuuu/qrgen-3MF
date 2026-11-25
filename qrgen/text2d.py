from __future__ import annotations

from fontTools.pens.basePen import BasePen
from shapely.affinity import scale as scale_geom
from shapely.affinity import translate
from shapely.geometry import LineString, Polygon
from shapely.ops import polygonize, unary_union

from qrgen.errors import InputError
from qrgen.fonts import open_font


class _FlattenPen(BasePen):
    def __init__(self, glyph_set, tolerance: float):
        super().__init__(glyph_set)
        self.tolerance = tolerance
        self.contours: list[list[tuple[float, float]]] = []
        self._pts: list[tuple[float, float]] = []

    def _moveTo(self, pt):
        self._pts = [(float(pt[0]), float(pt[1]))]

    def _lineTo(self, pt):
        self._pts.append((float(pt[0]), float(pt[1])))

    def _curveToOne(self, p1, p2, p3):
        self._flatten_cubic(self._pts[-1], p1, p2, p3, 0)

    def _qCurveToOne(self, p1, p2):
        self._flatten_quad(self._pts[-1], p1, p2, 0)

    def _closePath(self):
        self._store()

    def _endPath(self):
        self._store()

    def _store(self):
        cleaned = _dedupe(self._pts, tol=0.4)
        if len(cleaned) >= 3 and abs(_signed_area(cleaned)) > 1.0:
            self.contours.append(cleaned)
        self._pts = []

    def _flatten_quad(self, p0, p1, p2, depth: int):
        if depth >= 10 or _point_line_distance(p1, p0, p2) <= self.tolerance:
            self._pts.append((float(p2[0]), float(p2[1])))
            return
        a = _mid(p0, p1)
        b = _mid(p1, p2)
        m = _mid(a, b)
        self._flatten_quad(p0, a, m, depth + 1)
        self._flatten_quad(m, b, p2, depth + 1)

    def _flatten_cubic(self, p0, p1, p2, p3, depth: int):
        if depth >= 10 or (
            _point_line_distance(p1, p0, p3) <= self.tolerance
            and _point_line_distance(p2, p0, p3) <= self.tolerance
        ):
            self._pts.append((float(p3[0]), float(p3[1])))
            return
        p01 = _mid(p0, p1)
        p12 = _mid(p1, p2)
        p23 = _mid(p2, p3)
        p012 = _mid(p01, p12)
        p123 = _mid(p12, p23)
        p0123 = _mid(p012, p123)
        self._flatten_cubic(p0, p01, p012, p0123, depth + 1)
        self._flatten_cubic(p0123, p123, p23, p3, depth + 1)


def _mid(a, b):
    return ((a[0] + b[0]) * 0.5, (a[1] + b[1]) * 0.5)


def _point_line_distance(point, start, end) -> float:
    px, py = float(point[0]), float(point[1])
    x1, y1 = float(start[0]), float(start[1])
    x2, y2 = float(end[0]), float(end[1])
    dx, dy = x2 - x1, y2 - y1
    length = (dx * dx + dy * dy) ** 0.5
    if length < 1e-9:
        return ((px - x1) ** 2 + (py - y1) ** 2) ** 0.5
    return abs(dy * px - dx * py + x2 * y1 - y2 * x1) / length


def _dedupe(points: list[tuple[float, float]], tol: float) -> list[tuple[float, float]]:
    cleaned: list[tuple[float, float]] = []
    for point in points:
        if not cleaned or abs(point[0] - cleaned[-1][0]) > tol or abs(point[1] - cleaned[-1][1]) > tol:
            cleaned.append(point)
    if len(cleaned) >= 2 and abs(cleaned[0][0] - cleaned[-1][0]) <= tol and abs(cleaned[0][1] - cleaned[-1][1]) <= tol:
        cleaned.pop()
    return cleaned


def _signed_area(points: list[tuple[float, float]]) -> float:
    area = 0.0
    count = len(points)
    for index in range(count):
        x1, y1 = points[index]
        x2, y2 = points[(index + 1) % count]
        area += x1 * y2 - x2 * y1
    return area * 0.5


def _winding(x: float, y: float, contours: list[list[tuple[float, float]]]) -> int:
    wind = 0
    for contour in contours:
        count = len(contour)
        for index in range(count):
            x1, y1 = contour[index]
            x2, y2 = contour[(index + 1) % count]
            if y1 <= y < y2 or y2 <= y < y1:
                cross = (y - y1) * (x2 - x1) / (y2 - y1) + x1
                if cross > x:
                    wind += 1 if y1 < y2 else -1
    return wind


def _as_polygons(geometry):
    if geometry.is_empty:
        return
    kind = geometry.geom_type
    if kind == "Polygon":
        yield geometry
    elif kind == "MultiPolygon":
        yield from geometry.geoms
    elif kind == "GeometryCollection":
        for part in geometry.geoms:
            yield from _as_polygons(part)


def contours_to_polygons(contours: list[list[tuple[float, float]]], evenodd: bool = False) -> list[Polygon]:
    lines = []
    for contour in contours:
        ring = list(contour)
        if ring[0] != ring[-1]:
            ring.append(ring[0])
        if len(ring) >= 4:
            lines.append(LineString(ring))
    if not lines:
        return []
    faces = list(polygonize(unary_union(lines)))
    kept = []
    for face in faces:
        point = face.representative_point()
        wind = _winding(point.x, point.y, contours)
        if (abs(wind) % 2 == 1) if evenodd else wind != 0:
            kept.append(face)
    if not kept:
        return []
    geometry = unary_union(kept)
    polygons = []
    for polygon in _as_polygons(geometry):
        if not polygon.is_valid:
            polygon = polygon.buffer(0)
        for part in _as_polygons(polygon):
            if part.area > 1.0 and not part.is_empty:
                polygons.append(part)
    return polygons


class FontFace:
    def __init__(self, path: str):
        self.path = path
        self.font = open_font(path)
        self.glyph_set = self.font.getGlyphSet()
        self.cmap = self.font.getBestCmap() or {}
        self.hmtx = self.font["hmtx"].metrics
        self.upem = int(self.font["head"].unitsPerEm)
        self._glyph_cache: dict[str, list[Polygon]] = {}
        os2 = self.font["OS/2"]
        self.ascender = int(getattr(os2, "sTypoAscender", 0) or self.font["hhea"].ascender)
        self.descender = int(getattr(os2, "sTypoDescender", 0) or self.font["hhea"].descender)
        self.cap_units = self._cap_units()
        self.line_units = self._line_units()

    def _cap_units(self) -> float:
        os2 = self.font["OS/2"]
        cap = int(getattr(os2, "sCapHeight", 0) or 0)
        if cap > 0:
            return float(cap)
        polygons = self.glyph_polygons(self._glyph_name("H") or ".notdef")
        if not polygons:
            return self.upem * 0.7
        top = max(poly.bounds[3] for poly in polygons)
        return max(top, 1.0)

    def _line_units(self) -> float:
        hhea = self.font["hhea"]
        return float(hhea.ascent - hhea.descent + max(hhea.lineGap, 0)) or self.upem * 1.2

    def _glyph_name(self, char: str) -> str | None:
        return self.cmap.get(ord(char))

    def glyph_polygons(self, glyph_name: str) -> list[Polygon]:
        cached = self._glyph_cache.get(glyph_name)
        if cached is not None:
            return cached
        pen = _FlattenPen(self.glyph_set, tolerance=max(2.0, self.upem / 250))
        self.glyph_set[glyph_name].draw(pen)
        polygons = contours_to_polygons(pen.contours)
        self._glyph_cache[glyph_name] = polygons
        return polygons

    def advance(self, glyph_name: str) -> float:
        return float(self.hmtx[glyph_name][0])

    def kerning(self, left: str, right: str) -> float:
        if "kern" not in self.font:
            return 0.0
        for table in self.font["kern"].kernTables:
            kerns = getattr(table, "kernTable", None)
            if kerns:
                return float(kerns.get((left, right), 0))
        return 0.0


_FACE_CACHE: dict[str, FontFace] = {}


def get_face(path: str) -> FontFace:
    face = _FACE_CACHE.get(path)
    if face is None:
        face = FontFace(path)
        _FACE_CACHE[path] = face
    return face


def render_text(text: str, font_path: str, cap_mm: float, align_h: str) -> tuple[list[Polygon], list[str]]:
    """Zwraca wielokąty w mm, z linią bazową pierwszej linii na y=0."""
    stripped = text.strip("\n")
    if not stripped.strip():
        return [], []
    face = get_face(font_path)
    scale = cap_mm / face.cap_units
    warnings: list[str] = []
    missing: list[str] = []
    line_gap = face.line_units * scale
    placed: list[Polygon] = []

    lines = stripped.split("\n")
    rendered_lines: list[tuple[list[Polygon], float]] = []
    max_advance = 0.0

    for line_index, line in enumerate(lines):
        x_units = 0.0
        previous: str | None = None
        line_polys: list[Polygon] = []
        for char in line:
            if char == " ":
                space = face._glyph_name(" ")
                x_units += face.advance(space) if space else face.upem * 0.33
                previous = None
                continue
            glyph_name = face._glyph_name(char)
            if glyph_name is None:
                if char not in missing:
                    missing.append(char)
                previous = None
                continue
            if previous:
                x_units += face.kerning(previous, glyph_name)
            y_units = -line_index * face.line_units
            for polygon in face.glyph_polygons(glyph_name):
                line_polys.append(translate(polygon, x_units, y_units))
            x_units += face.advance(glyph_name)
            previous = glyph_name
        rendered_lines.append((line_polys, x_units))
        max_advance = max(max_advance, x_units)

    for line_polys, advance in rendered_lines:
        if align_h == "center":
            shift = (max_advance - advance) * 0.5
        elif align_h == "right":
            shift = max_advance - advance
        else:
            shift = 0.0
        for polygon in line_polys:
            moved = translate(polygon, shift, 0.0)
            placed.append(scale_geom(moved, scale, scale, origin=(0.0, 0.0)))

    if missing:
        shown = "".join(missing[:12])
        warnings.append(f"Font nie ma znaków: {shown}. Zostały pominięte.")
    if not placed and stripped.strip():
        raise InputError("Z tego tekstu nie da się zbudować liter w wybranym foncie.")
    return placed, warnings
