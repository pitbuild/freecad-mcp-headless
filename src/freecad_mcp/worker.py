"""Worker process that runs *inside* FreeCAD.

Started by :mod:`freecad_mcp.bridge` as ``freecadcmd worker.py``. It connects back to
the server over loopback TCP, authenticates with a one-time token and then serves
JSON lines until the server closes the connection.

This file must stay standalone: FreeCAD's embedded interpreter does not see the
virtualenv the MCP server runs in, so only the standard library and FreeCAD modules
may be imported at module level. ``matplotlib`` is imported lazily for PNG output.
"""

from __future__ import annotations

import io
import json
import math
import os
import socket
import sys
import traceback

import FreeCAD  # type: ignore  # provided by freecadcmd

PROTOCOL_VERSION = 1
MAX_TRACEBACK = 4000


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def _jsonable(value, depth: int = 0):
    """Convert FreeCAD values into plain JSON types."""
    if depth > 6:
        return str(value)
    if value is None or isinstance(value, (bool, int, str)):
        return value
    if isinstance(value, float):
        if math.isnan(value) or math.isinf(value):
            return None
        return round(value, 6)
    if isinstance(value, (list, tuple, set)):
        return [_jsonable(v, depth + 1) for v in value]
    if isinstance(value, dict):
        return {str(k): _jsonable(v, depth + 1) for k, v in value.items()}
    for attr in ("x", "y", "z"):
        if hasattr(value, attr) and not hasattr(value, "__len__"):
            try:
                return {axis: _jsonable(getattr(value, axis), depth + 1) for axis in ("x", "y", "z")}
            except Exception:
                break
    if hasattr(value, "Value"):
        try:
            return _jsonable(value.Value, depth + 1)
        except Exception:
            pass
    if hasattr(value, "Length"):
        try:
            return _jsonable(value.Length, depth + 1)
        except Exception:
            pass
    return str(value)


def _vec(v):
    return {"x": round(v.x, 6), "y": round(v.y, 6), "z": round(v.z, 6)}


class WorkerError(Exception):
    def __init__(self, message: str, kind: str = "worker_error") -> None:
        super().__init__(message)
        self.kind = kind


def _documents() -> dict:
    return FreeCAD.listDocuments()


def _doc(name: str | None = None):
    """Resolve a document by internal name, label or file stem."""
    if not name:
        doc = FreeCAD.ActiveDocument
        if doc is None:
            raise WorkerError("no document is open; pass a document name", "no_document")
        return doc
    docs = _documents()
    if name in docs:
        return docs[name]
    wanted = name.strip().lower()
    for doc in docs.values():
        if doc.Label.lower() == wanted or doc.Name.lower() == wanted:
            return doc
    # FreeCAD rewrites file stems into valid internal names (fc-smoke -> fc_smoke)
    normalised = "".join(c if c.isalnum() or c == "_" else "_" for c in name)
    for doc in docs.values():
        if doc.Name.lower() == normalised.lower():
            return doc
        try:
            if doc.FileName and os.path.splitext(os.path.basename(doc.FileName))[0].lower() == wanted:
                return doc
        except Exception:
            continue
    raise WorkerError(
        f"unknown document {name!r}; open documents: {', '.join(docs) or '(none)'}",
        "unknown_document",
    )


CONTAINER_TYPES = {
    "App::Part",
    "App::DocumentObjectGroup",
    "App::Origin",
    "App::Line",
    "App::Plane",
    "App::Point",
    "App::LinkGroup",
}


def _shapes(doc, objects=None):
    """Yield (object, shape) for measurable shapes.

    Containers (App::Part) and reference geometry (axes, planes) are skipped: their
    shape duplicates the children and would double every volume and blow up the
    bounding box with infinite extents.
    """
    if objects:
        wanted = {o.strip() for o in objects}
    else:
        wanted = None
    for obj in doc.Objects:
        if wanted is not None and obj.Name not in wanted and obj.Label not in wanted:
            continue
        if obj.TypeId in CONTAINER_TYPES:
            continue
        shape = getattr(obj, "Shape", None)
        if shape is None:
            continue
        try:
            if shape.isNull():
                continue
            if not shape.Faces and not shape.Solids:
                continue
        except Exception:
            continue
        yield obj, shape


def _doc_summary(doc) -> dict:
    return {
        "name": doc.Name,
        "label": doc.Label,
        "file": doc.FileName or None,
        "objects": len(doc.Objects),
        "modified": bool(doc.isTouched()),
    }


def _bbox_of(shapes):
    from FreeCAD import BoundBox  # local import keeps module import cheap

    box = None
    for _, shape in shapes:
        try:
            b = shape.BoundBox
        except Exception:
            continue
        if b is None or b.isValid() is False:
            continue
        if box is None:
            box = BoundBox(b.XMin, b.YMin, b.ZMin, b.XMax, b.YMax, b.ZMax)
        else:
            box.add(b)
    return box


# --------------------------------------------------------------------------- #
# methods
# --------------------------------------------------------------------------- #
def m_ping(params):
    return {"pong": True, "documents": list(_documents())}


def m_info(params):
    version = FreeCAD.Version()
    return {
        "worker": {
            "protocol": PROTOCOL_VERSION,
            "pid": os.getpid(),
            "python": sys.version.split()[0],
            "executable": sys.executable,
        },
        "freecad": {
            "version": ".".join(str(v) for v in version[:3]),
            "build": version[3] if len(version) > 3 else None,
            "revision": version[4] if len(version) > 4 else None,
        },
        "documents": [_doc_summary(d) for d in _documents().values()],
    }


def m_open(params):
    path = params["path"]
    name = params.get("name")
    lower = path.lower()
    if lower.endswith((".fcstd", ".fcstd1")):
        doc = FreeCAD.openDocument(path, name) if name else FreeCAD.openDocument(path)
    else:
        # In console mode FreeCAD's OpenHandler for foreign formats routes through the
        # GUI, so import explicitly into a fresh document instead.
        import Import
        import Mesh

        label = name or os.path.splitext(os.path.basename(path))[0]
        doc = FreeCAD.newDocument(label)
        if lower.endswith((".stl", ".obj", ".ply", ".amf", ".3mf")):
            Mesh.insert(path, doc.Name)
        else:
            Import.insert(path, doc.Name)
    doc.recompute()
    result = _doc_summary(doc)
    result["kinds"] = sorted({o.TypeId for o in doc.Objects})
    return result


def m_list_documents(params):
    return {"documents": [_doc_summary(d) for d in _documents().values()]}


def m_close(params):
    doc = _doc(params.get("document"))
    name = doc.Name
    FreeCAD.closeDocument(name)
    return {"closed": name, "documents": list(_documents())}


def m_save(params):
    doc = _doc(params.get("document"))
    path = params.get("path")
    if path:
        doc.saveAs(path)
    else:
        doc.save()
    return {"saved": doc.FileName, "document": doc.Name}


def m_inspect(params):
    doc = _doc(params.get("document"))
    objects = []
    for obj in doc.Objects:
        entry = {
            "name": obj.Name,
            "label": obj.Label,
            "type": obj.TypeId,
            "visible": bool(getattr(obj, "Visibility", True)),
        }
        shape = getattr(obj, "Shape", None)
        if shape is not None:
            try:
                if not shape.isNull():
                    b = shape.BoundBox
                    entry["shape"] = {
                        "volume_mm3": round(shape.Volume, 4),
                        "area_mm2": round(shape.Area, 4),
                        "bbox_mm": [round(b.XLength, 4), round(b.YLength, 4), round(b.ZLength, 4)],
                        "solids": len(shape.Solids),
                        "faces": len(shape.Faces),
                        "edges": len(shape.Edges),
                    }
            except Exception:
                entry["shape"] = {"error": "shape could not be inspected"}
        expressions = []
        try:
            for prop, expr in obj.ExpressionEngine:
                expressions.append({"property": prop, "expression": expr})
        except Exception:
            pass
        if expressions:
            entry["expressions"] = expressions
        objects.append(entry)
    return {
        "document": _doc_summary(doc),
        "objects": objects,
        "timeline": [o.Name for o in doc.Objects],
    }


def m_measure(params):
    doc = _doc(params.get("document"))
    shapes = list(_shapes(doc, params.get("objects")))
    if not shapes:
        raise WorkerError("no solid geometry found in the requested scope", "empty_selection")
    total_volume = 0.0
    total_area = 0.0
    faces = edges = vertices = 0
    per_object = []
    for obj, shape in shapes:
        volume = float(shape.Volume)
        area = float(shape.Area)
        total_volume += volume
        total_area += area
        faces += len(shape.Faces)
        edges += len(shape.Edges)
        vertices += len(shape.Vertexes)
        try:
            com = _vec(shape.CenterOfMass)
        except Exception:
            com = None
        per_object.append(
            {
                "name": obj.Name,
                "label": obj.Label,
                "volume_mm3": round(volume, 4),
                "area_mm2": round(area, 4),
                "center_of_mass_mm": com,
                "faces": len(shape.Faces),
                "edges": len(shape.Edges),
                "vertices": len(shape.Vertexes),
            }
        )
    box = _bbox_of(shapes)
    bbox = None
    if box is not None:
        bbox = {
            "min_mm": [round(box.XMin, 4), round(box.YMin, 4), round(box.ZMin, 4)],
            "max_mm": [round(box.XMax, 4), round(box.YMax, 4), round(box.ZMax, 4)],
            "size_mm": [round(box.XLength, 4), round(box.YLength, 4), round(box.ZLength, 4)],
            "diagonal_mm": round(math.sqrt(box.XLength**2 + box.YLength**2 + box.ZLength**2), 4),
        }
    return {
        "document": doc.Name,
        "units": "mm",
        "objects": per_object,
        "total": {
            "volume_mm3": round(total_volume, 4),
            "area_mm2": round(total_area, 4),
            "faces": faces,
            "edges": edges,
            "vertices": vertices,
        },
        "bbox": bbox,
    }


def m_bounding_box(params):
    doc = _doc(params.get("document"))
    shapes = list(_shapes(doc, params.get("objects")))
    if not shapes:
        raise WorkerError("no solid geometry found in the requested scope", "empty_selection")
    box = _bbox_of(shapes)
    if box is None:
        raise WorkerError("could not compute a bounding box", "empty_selection")
    return {
        "size_mm": [round(box.XLength, 4), round(box.YLength, 4), round(box.ZLength, 4)],
        "min_mm": [round(box.XMin, 4), round(box.YMin, 4), round(box.ZMin, 4)],
        "max_mm": [round(box.XMax, 4), round(box.YMax, 4), round(box.ZMax, 4)],
        "center_mm": [
            round((box.XMin + box.XMax) / 2, 4),
            round((box.YMin + box.YMax) / 2, 4),
            round((box.ZMin + box.ZMax) / 2, 4),
        ],
    }


def m_export(params):
    import Import
    import Mesh

    doc = _doc(params.get("document"))
    path = params["path"]
    fmt = params.get("format") or os.path.splitext(path)[1].lstrip(".").lower()
    shapes = [obj for obj, _ in _shapes(doc, params.get("objects"))]
    if not shapes:
        shapes = [o for o in doc.Objects if hasattr(o, "Shape")]
    if not shapes:
        raise WorkerError("document has no exportable objects", "empty_selection")

    if fmt == "fcstd":
        doc.saveAs(path)
    elif fmt in {"step", "stp", "iges", "igs", "brep", "dxf", "svg", "amf"}:
        Import.export(shapes, path)
    elif fmt in {"stl", "obj", "ply", "3mf"}:
        Mesh.export(shapes, path)
    else:
        raise WorkerError(f"unsupported export format {fmt!r}", "unsupported_format")
    if not os.path.exists(path):
        raise WorkerError(f"FreeCAD did not write {path}", "export_failed")
    return {
        "path": path,
        "format": fmt,
        "bytes": os.path.getsize(path),
        "objects": [o.Name for o in shapes],
    }


def m_parameters(params):
    doc = _doc(params.get("document"))
    entries = []
    for obj in doc.Objects:
        try:
            pairs = list(obj.ExpressionEngine)
        except Exception:
            continue
        for prop, expr in pairs:
            value = None
            try:
                value = _jsonable(getattr(obj, prop))
            except Exception:
                pass
            entries.append(
                {
                    "object": obj.Name,
                    "property": prop,
                    "expression": expr,
                    "value": value,
                    "reference": f"{obj.Name}.{prop}",
                }
            )
    sheets = []
    for obj in doc.Objects:
        if obj.TypeId != "Spreadsheet::Sheet":
            continue
        cells = {}
        try:
            for alias in obj.aliases:
                cells[alias] = _jsonable(obj.get(alias))
        except Exception:
            pass
        sheets.append({"name": obj.Name, "aliases": cells})
    return {"document": doc.Name, "expressions": entries, "spreadsheets": sheets}


def m_set_parameter(params):
    doc = _doc(params.get("document"))
    reference = params["reference"]
    expression = str(params["value"])
    if "." in reference:
        obj_name, prop = reference.split(".", 1)
        obj = doc.getObject(obj_name)
        if obj is None:
            raise WorkerError(f"object {obj_name!r} not found", "unknown_object")
        if prop not in obj.PropertiesList:
            raise WorkerError(f"{obj_name} has no property {prop!r}", "unknown_property")
        obj.setExpression(prop, expression)
    else:
        target = None
        for obj in doc.Objects:
            if obj.TypeId == "Spreadsheet::Sheet":
                try:
                    if reference in obj.aliases:
                        target = obj
                        break
                except Exception:
                    continue
        if target is None:
            raise WorkerError(
                f"no expression reference {reference!r}; use 'Object.Property' or a spreadsheet alias",
                "unknown_parameter",
            )
        target.set(reference, expression)
    doc.recompute()
    return {"document": doc.Name, "reference": reference, "value": expression, "recomputed": True}


def m_recompute(params):
    doc = _doc(params.get("document"))
    doc.recompute()
    return {"document": doc.Name, "recomputed": True, "touched": bool(doc.isTouched())}


def m_execute(params):
    # A snippet may create its own document, so no document is required here.
    try:
        doc = _doc(params.get("document"))
    except WorkerError:
        doc = None
    code = params["code"]
    buffer = io.StringIO()
    namespace = {
        "__name__": "__freecad_mcp__",
        "FreeCAD": FreeCAD,
        "App": FreeCAD,
        "doc": doc,
        "Gui": None,
    }
    old_stdout = sys.stdout
    result = None
    try:
        sys.stdout = buffer
        exec(compile(code, "<freecad-mcp>", "exec"), namespace)
        result = namespace.get("result")
    finally:
        sys.stdout = old_stdout
    output = buffer.getvalue()
    active = namespace.get("doc") or FreeCAD.ActiveDocument
    return {
        "document": active.Name if active is not None else None,
        "stdout": output[-8000:],
        "result": _jsonable(result) if result is not None else None,
        "names": sorted(
            k for k in namespace
            if not k.startswith("__") and k not in {"FreeCAD", "App", "doc", "Gui"}
        ),
    }


def m_undo(params):
    doc = _doc(params.get("document"))
    if not doc.UndoMode:
        raise WorkerError("undo is disabled for this document", "undo_disabled")
    doc.undo()
    return {"document": doc.Name, "undone": True}


def m_redo(params):
    doc = _doc(params.get("document"))
    doc.redo()
    return {"document": doc.Name, "redone": True}


# --------------------------------------------------------------------------- #
# rendering
# --------------------------------------------------------------------------- #
VIEWS = {
    "iso": (26.0, -55.0),
    "front": (0.0, -90.0),
    "back": (0.0, 90.0),
    "left": (0.0, 180.0),
    "right": (0.0, 0.0),
    "top": (89.9, -90.0),
    "bottom": (-89.9, -90.0),
}


def _tessellate(shapes, deviation):
    verts = []
    tris = []
    for _, shape in shapes:
        try:
            points, facets = shape.tessellate(deviation)
        except Exception:
            continue
        offset = len(verts)
        verts.extend((p.x, p.y, p.z) for p in points)
        tris.extend([offset + i for i in facet] for facet in facets)
    return verts, tris


def _project(verts, elev_deg, azim_deg):
    ce = math.cos(math.radians(elev_deg))
    se = math.sin(math.radians(elev_deg))
    ca = math.cos(math.radians(azim_deg))
    sa = math.sin(math.radians(azim_deg))
    out = []
    for x, y, z in verts:
        rx = x * ca - y * sa
        ry = x * sa + y * ca
        sy = ry * ce - z * se
        depth = ry * se + z * ce
        out.append((rx, sy, depth))
    return out


def _shade(normal, light=(0.35, -0.55, 0.75)):
    nx, ny, nz = normal
    ln = math.sqrt(sum(c * c for c in light)) or 1.0
    nn = math.sqrt(nx * nx + ny * ny + nz * nz) or 1.0
    dot = (nx * light[0] + ny * light[1] + nz * light[2]) / (ln * nn)
    factor = 0.42 + 0.58 * max(0.0, dot)
    base = (0.47, 0.65, 0.82)
    return tuple(min(1.0, c * factor) for c in base)


def _scene(shapes, elev, azim, max_triangles):
    verts, tris = _tessellate(shapes, _deviation_for(shapes))
    if not tris:
        raise WorkerError("nothing to render", "empty_selection")
    if len(tris) > max_triangles:
        step = max(1, len(tris) // max_triangles)
        tris = tris[::step]
    projected = _project(verts, elev, azim)
    polys = []
    for tri in tris:
        a, b, c = projected[tri[0]], projected[tri[1]], projected[tri[2]]
        ux, uy, uz = b[0] - a[0], b[1] - a[1], b[2] - a[2]
        vx, vy, vz = c[0] - a[0], c[1] - a[1], c[2] - a[2]
        normal = (uy * vz - uz * vy, uz * vx - ux * vz, ux * vy - uy * vx)
        depth = (a[2] + b[2] + c[2]) / 3.0
        polys.append((depth, [(p[0], p[1]) for p in (a, b, c)], _shade(normal)))
    polys.sort(key=lambda item: item[0])
    return polys


def _deviation_for(shapes):
    box = _bbox_of(shapes)
    if box is None:
        return 0.5
    size = max(box.XLength, box.YLength, box.ZLength, 1.0)
    return max(size / 220.0, 0.05)


def _write_png(polys, path, width, height, note):
    try:
        import matplotlib

        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib.collections import PolyCollection
    except Exception as exc:  # pragma: no cover - depends on the FreeCAD build
        raise WorkerError(
            "PNG rendering needs matplotlib inside FreeCAD's Python. "
            "Use a .svg target instead, or install matplotlib for the FreeCAD interpreter. "
            f"({exc})",
            "renderer_unavailable",
        ) from exc

    xs = [p[0] for _, poly, _ in polys for p in poly]
    ys = [p[1] for _, poly, _ in polys for p in poly]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    spanx = max(maxx - minx, 1e-6)
    spany = max(maxy - miny, 1e-6)
    pad = 0.06 * max(spanx, spany)

    fig = plt.figure(figsize=(width / 100.0, height / 100.0), dpi=100)
    ax = fig.add_axes((0, 0, 1, 1))
    collection = PolyCollection(
        [poly for _, poly, _ in polys],
        facecolors=[colour for _, _, colour in polys],
        edgecolors=[(0.12, 0.18, 0.24, 0.35) for _ in polys],
        linewidths=0.2,
        antialiaseds=True,
    )
    ax.add_collection(collection)
    ax.set_xlim(minx - pad, maxx + pad)
    ax.set_ylim(miny - pad, maxy + pad)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.patch.set_facecolor("#f4f6f8")
    if note:
        fig.text(0.5, 0.02, note, ha="center", va="bottom", fontsize=7, color="#5a6672")
    fig.savefig(path, facecolor=fig.get_facecolor())
    plt.close(fig)


def _write_svg(polys, path, width, height, note):
    xs = [p[0] for _, poly, _ in polys for p in poly]
    ys = [p[1] for _, poly, _ in polys for p in poly]
    minx, maxx, miny, maxy = min(xs), max(xs), min(ys), max(ys)
    span = max(maxx - minx, maxy - miny, 1e-6)
    pad = 0.05 * span
    scale = min((width - 2 * pad) / span, (height - 2 * pad) / span)

    def tx(x):
        return (x - minx) * scale + (width - (maxx - minx) * scale) / 2

    def ty(y):
        return height - ((y - miny) * scale + (height - (maxy - miny) * scale) / 2)

    lines = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" '
        f'viewBox="0 0 {width} {height}">',
        '<rect width="100%" height="100%" fill="#f4f6f8"/>',
    ]
    for _, poly, colour in polys:
        points = " ".join(f"{tx(x):.2f},{ty(y):.2f}" for x, y in poly)
        fill = "#{:02x}{:02x}{:02x}".format(*(int(c * 255) for c in colour))
        lines.append(
            f'<polygon points="{points}" fill="{fill}" stroke="#1f2b36" '
            'stroke-opacity="0.25" stroke-width="0.25"/>'
        )
    if note:
        lines.append(
            f'<text x="{width/2:.0f}" y="{height-6}" font-family="sans-serif" font-size="8" '
            f'fill="#5a6672" text-anchor="middle">{note}</text>'
        )
    lines.append("</svg>")
    with open(path, "w", encoding="utf-8") as handle:
        handle.write("\n".join(lines))


def m_screenshot(params):
    doc = _doc(params.get("document"))
    path = params["path"]
    width = int(params.get("width") or 1200)
    height = int(params.get("height") or 800)
    view = (params.get("view") or "iso").lower()
    if view not in VIEWS:
        raise WorkerError(
            f"unknown view {view!r}; expected one of: {', '.join(sorted(VIEWS))}", "unknown_view"
        )
    shapes = list(_shapes(doc, params.get("objects")))
    if not shapes:
        raise WorkerError("no geometry to render", "empty_selection")
    elev, azim = VIEWS[view]
    if params.get("elevation") is not None:
        elev = float(params["elevation"])
    if params.get("azimuth") is not None:
        azim = float(params["azimuth"])
    polys = _scene(shapes, elev, azim, int(params.get("max_triangles") or 60000))
    box = _bbox_of(shapes)
    note = ""
    if box is not None:
        note = (
            f"{box.XLength:.1f} x {box.YLength:.1f} x {box.ZLength:.1f} mm  |  "
            f"view {view}  |  FreeCAD {'.'.join(str(v) for v in FreeCAD.Version()[:3])}"
        )
    if path.lower().endswith(".svg"):
        _write_svg(polys, path, width, height, note)
    else:
        _write_png(polys, path, width, height, note)
    return {
        "path": path,
        "bytes": os.path.getsize(path),
        "triangles": len(polys),
        "view": view,
        "size": [width, height],
        "note": note,
    }


# --------------------------------------------------------------------------- #
# dispatch + main loop
# --------------------------------------------------------------------------- #
METHODS = {
    "ping": m_ping,
    "info": m_info,
    "open": m_open,
    "list_documents": m_list_documents,
    "close": m_close,
    "save": m_save,
    "inspect": m_inspect,
    "measure": m_measure,
    "bounding_box": m_bounding_box,
    "export": m_export,
    "parameters": m_parameters,
    "set_parameter": m_set_parameter,
    "recompute": m_recompute,
    "execute": m_execute,
    "undo": m_undo,
    "redo": m_redo,
    "screenshot": m_screenshot,
}


def serve(port: int, token: str) -> None:
    sock = socket.create_connection(("127.0.0.1", port), timeout=60)
    sock.settimeout(None)
    stream = sock.makefile("rwb")

    def send(payload: dict) -> None:
        stream.write((json.dumps(payload, allow_nan=False) + "\n").encode("utf-8"))
        stream.flush()

    send(
        {
            "event": "ready",
            "token": token,
            "protocol": PROTOCOL_VERSION,
            "pid": os.getpid(),
            "freecad": ".".join(str(v) for v in FreeCAD.Version()[:3]),
            "python": sys.version.split()[0],
        }
    )
    while True:
        line = stream.readline()
        if not line:
            break
        try:
            request = json.loads(line.decode("utf-8"))
        except Exception:
            continue
        request_id = request.get("id")
        method = request.get("method")
        params = request.get("params") or {}
        if method == "shutdown":
            send({"id": request_id, "ok": True, "result": {"bye": True}})
            break
        handler = METHODS.get(method)
        try:
            if handler is None:
                raise WorkerError(f"unknown method {method!r}", "unknown_method")
            result = handler(params)
            send({"id": request_id, "ok": True, "result": _jsonable(result)})
        except WorkerError as exc:
            send(
                {
                    "id": request_id,
                    "ok": False,
                    "error": {"kind": exc.kind, "type": type(exc).__name__, "message": str(exc)},
                }
            )
        except Exception as exc:  # noqa: BLE001 - the worker must survive any failure
            send(
                {
                    "id": request_id,
                    "ok": False,
                    "error": {
                        "kind": "freecad_error",
                        "type": type(exc).__name__,
                        "message": str(exc),
                        "traceback": traceback.format_exc()[-MAX_TRACEBACK:],
                    },
                }
            )
    try:
        sock.close()
    except Exception:
        pass


def main() -> int:
    port = int(os.environ["FREECAD_MCP_PORT"])
    token = os.environ["FREECAD_MCP_TOKEN"]
    serve(port, token)
    return 0


# FreeCAD executes a script file without necessarily setting __name__ to
# "__main__", so the entry point is guarded by the environment instead.
if os.environ.get("FREECAD_MCP_PORT"):
    sys.exit(main())
