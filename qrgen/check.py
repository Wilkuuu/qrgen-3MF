"""Sprawdza litery, otwory, objętość siatki i plik 3MF."""

from __future__ import annotations

import re
import zipfile
from io import BytesIO

import numpy as np
from shapely.ops import unary_union

from qrgen.export_3mf import _objects, _relief_z, build_3mf
from qrgen.fonts import default_font_id, resolve_font
from qrgen.mesh import box_mesh, extrude_polygons, is_watertight, mesh_volume, plate_polygon
from qrgen.params import parse_params
from qrgen.scene import build_scene
from qrgen.text2d import render_text


def _bambu_matrix(text: str) -> np.ndarray:
    """Tak Bambu Studio czyta transform: 12 liczb kolumnami, nie wierszami specyfikacji."""
    values = [float(part) for part in text.split()]
    assert len(values) == 12, text
    matrix = np.eye(4)
    cursor = 0
    for column in range(4):
        for row in range(3):
            matrix[row, column] = values[cursor]
            cursor += 1
    return matrix


def _bambu_place(model_xml: str, name: str) -> np.ndarray:
    block = re.search(rf'<object [^>]*name="{name}".*?</object>', model_xml, re.S)
    assert block, name
    vertices = np.array(
        [(float(x), float(y), float(z)) for x, y, z in re.findall(r'<vertex x="([^"]+)" y="([^"]+)" z="([^"]+)"/>', block.group(0))],
        dtype=np.float64,
    )
    object_id = re.search(r'id="(\d+)"', block.group(0)).group(1)
    component = re.search(rf'<component objectid="{object_id}" transform="([^"]+)"', model_xml)
    assert component, name
    matrix = _bambu_matrix(component.group(1))
    points = np.column_stack([vertices, np.ones(len(vertices))])
    return (matrix @ points.T).T[:, :3]


def _ink_area(polygons) -> float:
    return float(unary_union(polygons).area)


def _bbox_area(polygons) -> float:
    min_x, min_y, max_x, max_y = unary_union(polygons).bounds
    return (max_x - min_x) * (max_y - min_y)


def main() -> None:
    font, _bold = resolve_font(default_font_id(), False)
    o_polys, warnings = render_text("O", str(font), 10.0, "left")
    assert o_polys, "brak litery O"
    assert not warnings
    o_area = _ink_area(o_polys)
    o_box = _bbox_area(o_polys)
    print(f"O area={o_area:.2f} bbox={o_box:.2f} ratio={o_area / o_box:.2f}")
    assert o_area < o_box * 0.72, "litera O nie ma otworu"

    sample = "Zażółć gęślą jaźń"
    polys, warnings = render_text(sample, str(font), 6.0, "center")
    assert polys, "brak polskich znaków"
    assert not warnings, warnings
    print(f"pangram polygons={len(polys)} area={_ink_area(polys):.2f}")

    for char in "aąeęoół":
        glyph, missing = render_text(char, str(font), 8.0, "left")
        assert glyph and not missing, char
        mesh = extrude_polygons(glyph, 0.0, 0.8)
        assert mesh is not None, char
        volume = mesh_volume(*mesh)
        area = _ink_area(glyph)
        print(f"  {char} vol={volume:.3f} area*h={area * 0.8:.3f} watertight={is_watertight(mesh[1])}")
        assert volume > 0, char
        assert abs(volume - area * 0.8) / (area * 0.8) < 0.03, char
        assert is_watertight(mesh[1]), char

    cube = box_mesh(0, 0, 0, 10, 5, 2)
    assert cube is not None
    assert abs(mesh_volume(*cube) - 100) < 1e-6
    assert is_watertight(cube[1])

    scene = build_scene(
        parse_params(
            {
                "url": "https://github.com/Wilkuuu/qrgen-3MF",
                "text": "QRGEN\nZażółć",
                "font_id": default_font_id(),
                "layout": "qr-left",
                "base_w": 90,
                "base_d": 40,
                "qr_size": 28,
                "color_text": "#E2A23A",
                "same_ink": False,
            }
        )
    )
    assert scene.qr_rects and scene.text_polygons
    assert len(scene.filament_colors) == 3
    # lewy górny róg macierzy QR (finder) musi leżeć w jakimś prostokącie
    top = max(y + h for _x, y, _w, h in scene.qr_rects)
    left = min(x for x, _y, _w, _h in scene.qr_rects)
    assert any(x <= left + 0.01 and y + h >= top - 0.01 for x, y, _w, h in scene.qr_rects)

    payload = build_3mf(scene)
    with zipfile.ZipFile(BytesIO(payload)) as archive:
        names = set(archive.namelist())
        assert "3D/3dmodel.model" in names
        assert "Metadata/model_settings.config" in names
        assert "Metadata/project_settings.config" in names
        model = archive.read("3D/3dmodel.model").decode("utf-8")
    assert model.count("<object ") == 4
    assert model.count("<item ") == 1
    assert 'paint_color="4"' in model and 'paint_color="8"' in model and 'paint_color="0C"' in model
    assert 'name="Podstawa"' in model and 'name="QR"' in model and 'name="Tekst"' in model
    records = _objects(scene)
    qr = next(item for item in records if item["name"] == "QR")
    text = next(item for item in records if item["name"] == "Tekst")
    qr_min = qr["vertices"].min(axis=0) + qr["center"]
    text_min = text["vertices"].min(axis=0) + text["center"]
    print(f"QR world min={qr_min} text world min x={text_min[0]:.2f}")
    assert qr_min[0] > 1.0, "QR nadal startuje od x=0"
    assert text_min[0] > qr_min[0] + 5, "tekst nie jest po prawej stronie QR"
    expected_z = _relief_z(scene.params.base_h, scene.params.qr_layer)
    assert abs(float(qr_min[2]) - expected_z) < 0.05, qr_min[2]
    settings = zipfile.ZipFile(BytesIO(payload)).read("Metadata/model_settings.config").decode("utf-8")
    assert settings.count("<object ") == 1
    assert settings.count("<part ") == 3
    placed = _bambu_place(model, "Podstawa")
    qr_placed = _bambu_place(model, "QR")
    span = placed.max(axis=0) - placed.min(axis=0)
    assert abs(span[0] - scene.params.base_w) < 0.05, span
    assert abs(span[1] - scene.params.base_d) < 0.05, span
    assert abs(float(placed.min(axis=0)[2])) < 0.05, placed.min(axis=0)
    assert abs(float(qr_placed.min(axis=0)[2]) - expected_z) < 0.05, qr_placed.min(axis=0)
    assert float(qr_placed.min(axis=0)[2]) > float(placed.max(axis=0)[2]) - 0.05
    print(f"bambu plate span={span} qr z={qr_placed.min(axis=0)[2]:.2f}")
    print(f"3mf bytes={len(payload)} objects=3 nadruk od z={expected_z:.2f}")

    plate = plate_polygon(50, 30, 8)
    rounded = extrude_polygons([plate], 0.0, 2.0)
    assert rounded is not None and is_watertight(rounded[1])
    assert mesh_volume(*rounded) < 50 * 30 * 2 * 0.98
    print(f"zaokrąglenie vol={mesh_volume(*rounded):.1f}")

    icon = """<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 100 100">
      <circle cx="50" cy="50" r="40"/>
    </svg>"""
    graphic = build_scene(parse_params({
        "url": "",
        "text": "",
        "svg": icon,
        "svg_width": 20,
        "svg_x": 10,
        "svg_y": 6,
        "svg_same": False,
        "base_w": 40,
        "base_d": 40,
    }))
    bounds = unary_union(graphic.svg_polygons).bounds
    print(f"SVG bounds={tuple(round(v, 2) for v in bounds)}")
    assert abs((bounds[2] - bounds[0]) - 20) < 0.4
    assert abs(bounds[0] - 10) < 0.4
    assert abs(bounds[1] - 6) < 0.4
    ring = """<svg xmlns="http://www.w3.org/2000/svg"><path fill-rule="evenodd" d="M0 0 H100 V100 H0 Z M25 25 H75 V75 H25 Z"/></svg>"""
    ring_scene = build_scene(parse_params({"url": "", "text": "", "svg": ring, "svg_width": 10, "svg_x": 0, "svg_y": 0}))
    ring_area = unary_union(ring_scene.svg_polygons).area
    assert ring_area < 80, ring_area

    from app import app

    client = app.test_client()
    response = client.post("/api/preview", json={"text": "Test", "url": "https://example.com"})
    assert response.status_code == 200, response.get_json()
    body = response.get_json()
    assert "<svg" in body["svg"]
    assert "stroke-dasharray" not in body["svg"]
    parked = build_scene(parse_params({"text": "A", "url": "https://example.com", "qr_x": 4, "text_x": 6, "text_y": 2}))
    still = build_scene(parse_params({"text": "A", "url": "https://example.com"}))
    assert abs(min(x for x, *_rest in parked.qr_rects) - min(x for x, *_rest in still.qr_rects) - 4) < 0.05
    parked_text = unary_union(parked.text_polygons).bounds
    still_text = unary_union(still.text_polygons).bounds
    assert abs((parked_text[0] - still_text[0]) - 6) < 0.05
    assert abs((parked_text[1] - still_text[1]) - 2) < 0.05
    assert body["info"]["filaments"] >= 2
    stacked = build_scene(parse_params({"layout": "qr-top"}))
    assert stacked.qr_rects and stacked.text_polygons
    qr_bottom = min(y for _x, y, _w, _h in stacked.qr_rects)
    text_top = max(polygon.bounds[3] for polygon in stacked.text_polygons)
    assert qr_bottom + 0.2 >= text_top, (qr_bottom, text_top)
    assert any("QR zmniejszony" in note for note in stacked.warnings)
    print(f"qr-top ok, QR od y={qr_bottom:.1f}")

    bad = client.post("/api/preview", json={"text": "", "url": "", "base_w": 85})
    assert bad.status_code == 400
    print("podgląd HTTP ok")
    print("OK")


if __name__ == "__main__":
    main()
