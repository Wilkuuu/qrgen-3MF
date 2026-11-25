from __future__ import annotations

import json
from io import BytesIO
from zipfile import ZIP_DEFLATED, ZipFile

import numpy as np

from qrgen.errors import InputError
from qrgen.mesh import box_mesh, extrude_polygons, merge_meshes, plate_polygon
from qrgen.scene import Scene

PAINT = {1: "4", 2: "8", 3: "0C", 4: "1C", 5: "2C", 6: "3C", 7: "4C", 8: "5C"}
OVERLAP = 0.02


def build_3mf(scene: Scene) -> bytes:
    objects = _objects(scene)
    if not objects:
        raise InputError("Nie ma geometrii do zapisania.")
    model_xml = _model_xml(objects, scene)
    settings_xml = _model_settings(objects, scene)
    project_json = json.dumps(
        {
            "version": "2.3.5",
            "from": "project",
            "filament_colour": scene.filament_colors,
            "filament_type": ["PLA"] * len(scene.filament_colors),
            "filament_vendor": ["Generic"] * len(scene.filament_colors),
        },
        ensure_ascii=False,
    )
    buffer = BytesIO()
    with ZipFile(buffer, "w", compression=ZIP_DEFLATED) as archive:
        archive.writestr("[Content_Types].xml", _content_types())
        archive.writestr("_rels/.rels", _root_rels())
        archive.writestr("3D/3dmodel.model", model_xml)
        archive.writestr("Metadata/model_settings.config", settings_xml)
        archive.writestr("Metadata/project_settings.config", project_json)
    return buffer.getvalue()


def _objects(scene: Scene) -> list[dict]:
    params = scene.params
    objects = []
    if params.corner_radius > 0.05:
        base = extrude_polygons([plate_polygon(params.base_w, params.base_d, params.corner_radius)], 0.0, params.base_h)
        if base is None:
            raise InputError("Nie udało się zaokrąglić podstawy.")
    else:
        base = box_mesh(0.0, 0.0, 0.0, params.base_w, params.base_d, params.base_h)
    objects.append(_pack("Podstawa", params.color_base, scene.color_slot(params.color_base), base))

    if scene.qr_rects:
        z0 = _relief_z(params.base_h, params.qr_layer)
        parts = []
        for x, y, width, height in scene.qr_rects:
            mesh = box_mesh(x, y, z0, width, height, params.qr_layer)
            if mesh is not None:
                parts.append(mesh)
        if parts:
            objects.append(_pack("QR", params.color_qr, scene.color_slot(params.color_qr), merge_meshes(parts)))
    if scene.text_polygons:
        z0 = _relief_z(params.base_h, params.text_layer)
        mesh = extrude_polygons(scene.text_polygons, z0, params.text_layer)
        if mesh is None:
            raise InputError("Nie udało się wytłoczyć tekstu.")
        objects.append(_pack("Tekst", params.color_text, scene.color_slot(params.color_text), mesh))
    if scene.svg_polygons:
        z0 = _relief_z(params.base_h, params.svg_layer)
        mesh = extrude_polygons(scene.svg_polygons, z0, params.svg_layer)
        if mesh is None:
            raise InputError("Nie udało się wytłoczyć SVG.")
        objects.append(_pack("SVG", params.color_svg, scene.color_slot(params.color_svg), mesh))
    return objects


def _relief_z(base_height: float, layer: float) -> float:
    """Spód nadruku. Slicer ma dostać ten poziom jako początek QR, tekstu i SVG."""
    return base_height - min(OVERLAP, layer * 0.25)


def _pack(name: str, color: str, slot: int, mesh) -> dict:
    vertices, faces = mesh
    local, center = _center_mesh(vertices)
    return {
        "name": name,
        "color": color,
        "slot": slot,
        "vertices": local,
        "faces": faces,
        "center": center,
    }


def _center_mesh(vertices: np.ndarray):
    low = vertices.min(axis=0)
    high = vertices.max(axis=0)
    center = (low + high) / 2.0
    return vertices - center, center


def _model_xml(objects: list[dict], scene: Scene) -> str:
    colors = scene.filament_colors
    lines = [
        '<?xml version="1.0" encoding="UTF-8"?>',
        '<model unit="millimeter" xml:lang="pl-PL"',
        ' xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02"',
        ' xmlns:m="http://schemas.microsoft.com/3dmanufacturing/material/2015/02"',
        ' xmlns:slic3rpe="http://schemas.slic3r.org/3mf/2017/06"',
        ' xmlns:BambuStudio="http://schemas.bambulab.com/package/2021">',
        ' <metadata name="Application">BambuStudio-01.09.01.50</metadata>',
        ' <metadata name="BambuStudio:3mfVersion">1</metadata>',
        f' <metadata name="Title">{_xml(_title(scene))}</metadata>',
        ' <metadata name="Generator">qrgen-3MF</metadata>',
        " <resources>",
        '  <m:colorgroup id="1">',
    ]
    for color in colors:
        lines.append(f'   <m:color color="{_argb(color)}"/>')
    lines.append("  </m:colorgroup>")

    for index, obj in enumerate(objects, start=1):
        pindex = obj["slot"] - 1
        paint = PAINT[obj["slot"]]
        lines.append(
            f'  <object id="{index}" type="model" name="{_xml(obj["name"])}" pid="1" pindex="{pindex}">'
        )
        lines.append("   <mesh>")
        lines.append("    <vertices>")
        for x, y, z in obj["vertices"]:
            lines.append(f'     <vertex x="{_num(x)}" y="{_num(y)}" z="{_num(z)}"/>')
        lines.append("    </vertices>")
        lines.append("    <triangles>")
        for a, b, c in obj["faces"]:
            lines.append(
                f'     <triangle v1="{int(a)}" v2="{int(b)}" v3="{int(c)}" '
                f'pid="1" pindex="{pindex}" paint_color="{paint}" slic3rpe:mmu_segmentation="{paint}"/>'
            )
        lines.append("    </triangles>")
        lines.append("   </mesh>")
        lines.append("  </object>")
    assembly_id = len(objects) + 1
    lines.append(f'  <object id="{assembly_id}" type="model" name="{_xml(_title(scene))}">')
    lines.append("   <components>")
    for index, obj in enumerate(objects, start=1):
        lines.append(f'    <component objectid="{index}" transform="{_bambu_transform(obj["center"])}"/>')
    lines.append("   </components>")
    lines.append("  </object>")
    lines.append(" </resources>")
    lines.append(" <build>")
    lines.append(f'  <item objectid="{assembly_id}" transform="{_bambu_transform(np.zeros(3))}" printable="1"/>')
    lines.append(" </build>")
    lines.append("</model>")
    return "\n".join(lines)


def _model_settings(objects: list[dict], scene: Scene) -> str:
    """Jedna tabliczka, kilka części. Osobne obiekty slicer kładzie na stole i przykrywa nadruk podstawą."""
    assembly_id = len(objects) + 1
    lines = ['<?xml version="1.0" encoding="UTF-8"?>', "<config>"]
    lines.append(f' <object id="{assembly_id}">')
    lines.append(f'  <metadata key="name" value="{_xml(_title(scene))}"/>')
    lines.append('  <metadata key="extruder" value="1"/>')
    for index, obj in enumerate(objects, start=1):
        faces = int(len(obj["faces"]))
        name = _xml(obj["name"])
        slot = int(obj["slot"])
        lines.append(f'  <part id="{index}" subtype="normal_part">')
        lines.append(f'   <metadata key="name" value="{name}"/>')
        lines.append('   <metadata key="matrix" value="1 0 0 0 0 1 0 0 0 0 1 0 0 0 0 1"/>')
        lines.append(f'   <metadata key="extruder" value="{slot}"/>')
        lines.append(
            f'   <mesh_stat face_count="{faces}" edges_fixed="0" degenerate_facets="0" facets_removed="0" facets_reversed="0" backwards_edges="0"/>'
        )
        lines.append("  </part>")
    lines.append(" </object>")
    lines.append(" <plate>")
    lines.append('  <metadata key="plater_id" value="1"/>')
    lines.append('  <metadata key="plater_name" value=""/>')
    lines.append('  <metadata key="locked" value="false"/>')
    lines.append('  <metadata key="filament_map_mode" value="Auto For Flush"/>')
    lines.append("  <model_instance>")
    lines.append(f'   <metadata key="object_id" value="{assembly_id}"/>')
    lines.append('   <metadata key="instance_id" value="0"/>')
    lines.append('   <metadata key="identify_id" value="301"/>')
    lines.append("  </model_instance>")
    lines.append(" </plate>")
    lines.append(" <assemble>")
    lines.append(
        f'  <assemble_item object_id="{assembly_id}" instance_id="0" transform="{_bambu_transform(np.zeros(3))}" offset="0 0 0"/>'
    )
    lines.append(" </assemble>")
    lines.append("</config>")
    return "\n".join(lines)


def _content_types() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">
 <Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>
 <Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml"/>
 <Default Extension="config" ContentType="text/xml"/>
</Types>
"""


def _root_rels() -> str:
    return """<?xml version="1.0" encoding="UTF-8"?>
<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">
 <Relationship Target="/3D/3dmodel.model" Id="rel0" Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel"/>
</Relationships>
"""


def _title(scene: Scene) -> str:
    first = next((line.strip() for line in scene.params.text.splitlines() if line.strip()), "")
    if first:
        return first[:60]
    if scene.params.url:
        return "QR"
    return "Tabliczka"


def _argb(color: str) -> str:
    return "#FF" + color[1:].upper()


def _xml(value: str) -> str:
    return (
        value.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
    )


def _bambu_transform(center: np.ndarray) -> str:
    """Bambu i Orca czytają 12 liczb kolumnami: najpierw 3×3, potem przesunięcie.

    Zapis „1 0 0 x 0 1 0 y 0 0 1 z” ze specyfikacji 3MF wpada im w skalę
    i rozciąga model. Ta sama kolejność jest w pozycji na stole i w assemble.
    """
    x, y, z = (float(value) for value in center)
    return f"1 0 0 0 1 0 0 0 1 {_num(x)} {_num(y)} {_num(z)}"


def _num(value: float) -> str:
    number = float(value)
    if abs(number) < 5e-7:
        return "0"
    return f"{number:.4f}"


def filename_for(scene: Scene) -> str:
    import re

    source = next((line.strip() for line in scene.params.text.splitlines() if line.strip()), "")
    if not source:
        source = "qr"
    slug = re.sub(r"[^0-9A-Za-ząćęłńóśźżĄĆĘŁŃÓŚŹŻ]+", "-", source).strip("-").lower()
    if not slug:
        slug = "tabliczka"
    return f"{slug[:40]}.3mf"


def mesh_bounds(vertices: np.ndarray) -> tuple[float, float, float, float, float, float]:
    low = vertices.min(axis=0)
    high = vertices.max(axis=0)
    return tuple(float(v) for v in (*low, *high))
