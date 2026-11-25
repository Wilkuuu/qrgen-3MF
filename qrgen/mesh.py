from __future__ import annotations

import numpy as np
from shapely.geometry import Polygon, box as shapely_box
from shapely.geometry.polygon import orient as orient_polygon

try:
    import mapbox_earcut as earcut
except ImportError:  # pragma: no cover
    earcut = None

from qrgen.errors import InputError


def plate_polygon(width: float, depth: float, radius: float) -> Polygon:
    radius = max(0.0, min(radius, width / 2, depth / 2))
    if radius <= 0.05:
        return shapely_box(0.0, 0.0, width, depth)
    core = shapely_box(radius, radius, width - radius, depth - radius)
    segments = max(6, min(16, int(radius * 2)))
    polygon = core.buffer(radius, quad_segs=segments)
    if not polygon.is_valid:
        polygon = polygon.buffer(0)
    return polygon


def box_mesh(x: float, y: float, z: float, dx: float, dy: float, dz: float):
    if dx <= 0 or dy <= 0 or dz <= 0:
        return None
    vertices = np.array(
        [
            [x, y, z],
            [x + dx, y, z],
            [x + dx, y + dy, z],
            [x, y + dy, z],
            [x, y, z + dz],
            [x + dx, y, z + dz],
            [x + dx, y + dy, z + dz],
            [x, y + dy, z + dz],
        ],
        dtype=np.float64,
    )
    faces = np.array(
        [
            [0, 2, 1],
            [0, 3, 2],
            [4, 5, 6],
            [4, 6, 7],
            [0, 1, 5],
            [0, 5, 4],
            [1, 2, 6],
            [1, 6, 5],
            [2, 3, 7],
            [2, 7, 6],
            [3, 0, 4],
            [3, 4, 7],
        ],
        dtype=np.int64,
    )
    return _orient_outward(vertices, faces)


def extrude_polygon(polygon: Polygon, z0: float, height: float):
    if earcut is None:
        raise InputError("Brakuje biblioteki mapbox-earcut. Zainstaluj wymagania z requirements.txt.")
    if height <= 0 or polygon.is_empty or polygon.area < 1e-6:
        return None
    polygon = orient_polygon(polygon, sign=1.0)
    rings: list[list[tuple[float, float]]] = []
    for ring in (polygon.exterior, *polygon.interiors):
        coords = [(float(x), float(y)) for x, y in list(ring.coords)[:-1]]
        if len(coords) >= 3:
            rings.append(coords)
    if not rings:
        return None

    flat: list[tuple[float, float]] = []
    ends: list[int] = []
    for ring in rings:
        flat.extend(ring)
        ends.append(len(flat))

    vertices2d = np.asarray(flat, dtype=np.float32)
    ring_ends = np.asarray(ends, dtype=np.uint32)
    triangles = np.asarray(earcut.triangulate_float32(vertices2d, ring_ends), dtype=np.int64)
    if triangles.size < 3:
        return None
    triangles = triangles.reshape(-1, 3)
    keep = (
        (triangles[:, 0] != triangles[:, 1])
        & (triangles[:, 1] != triangles[:, 2])
        & (triangles[:, 2] != triangles[:, 0])
    )
    triangles = triangles[keep]
    if len(triangles) == 0:
        return None

    count = len(flat)
    bottom = np.column_stack([vertices2d.astype(np.float64), np.full(count, z0)])
    top = np.column_stack([vertices2d.astype(np.float64), np.full(count, z0 + height)])
    vertices = np.vstack([bottom, top])

    top_faces = triangles + count
    bottom_faces = triangles[:, ::-1]
    faces = [top_faces, bottom_faces]

    def wall(ring: list[tuple[float, float]], start_index: int):
        length = len(ring)
        quads = []
        for offset in range(length):
            a = start_index + offset
            b = start_index + ((offset + 1) % length)
            quads.append((a, b, b + count))
            quads.append((a, b + count, a + count))
        return np.asarray(quads, dtype=np.int64)

    cursor = 0
    for ring in rings:
        faces.append(wall(ring, cursor))
        cursor += len(ring)

    mesh = _orient_outward(vertices, np.vstack(faces))
    return mesh


def extrude_polygons(polygons: list[Polygon], z0: float, height: float):
    parts = []
    for polygon in polygons:
        for part in _iter_polygons(polygon):
            mesh = extrude_polygon(part, z0, height)
            if mesh is not None:
                parts.append(mesh)
    if not parts:
        return None
    return merge_meshes(parts)


def _iter_polygons(geometry):
    if geometry.is_empty:
        return
    if geometry.geom_type == "Polygon":
        yield geometry
    elif geometry.geom_type in {"MultiPolygon", "GeometryCollection"}:
        for part in geometry.geoms:
            yield from _iter_polygons(part)


def merge_meshes(parts: list[tuple[np.ndarray, np.ndarray]]):
    vertices = []
    faces = []
    offset = 0
    for mesh_vertices, mesh_faces in parts:
        vertices.append(mesh_vertices)
        faces.append(mesh_faces + offset)
        offset += len(mesh_vertices)
    return np.vstack(vertices), np.vstack(faces)


def mesh_volume(vertices: np.ndarray, faces: np.ndarray) -> float:
    a = vertices[faces[:, 0]]
    b = vertices[faces[:, 1]]
    c = vertices[faces[:, 2]]
    return float(np.sum(np.einsum("ij,ij->i", a, np.cross(b, c))) / 6.0)


def _orient_outward(vertices: np.ndarray, faces: np.ndarray):
    if mesh_volume(vertices, faces) < 0:
        faces = faces[:, ::-1].copy()
    return vertices, faces


def is_watertight(faces: np.ndarray) -> bool:
    from collections import Counter

    edges: Counter[tuple[int, int]] = Counter()
    for a, b, c in faces.tolist():
        for edge in ((a, b), (b, c), (c, a)):
            edges[tuple(sorted(edge))] += 1
    return bool(edges) and all(count == 2 for count in edges.values())
