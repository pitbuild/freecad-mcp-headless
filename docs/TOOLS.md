# Tool reference

With `--read-only`, only `freecad_info`, `open_document`, `close_document`,
`list_documents`, `inspect_document`, `measure`, `bounding_box`, `parameters` and
`worker_status` are exposed. Opening/closing changes only the worker session; exports and
screenshots are disabled because they write files.

Every tool returns JSON. Lengths are millimetres, areas mm², volumes mm³.
Document arguments accept the internal name (`fc_smoke`), the label, or the file stem.

## freecad_info

```json
{
  "worker": {"protocol": 1, "pid": 744261, "python": "3.11.14", "executable": "..."},
  "freecad": {"version": "1.1.4", "build": "20260928", "revision": "..."},
  "documents": [{"name": "Block", "label": "Block", "file": "/cad/block.FCStd", "objects": 2, "modified": false}]
}
```

Starts the worker on first use. A failed start returns `worker_unavailable` with the tail of the
worker log — usually a wrong `freecadcmd` path or a missing system library.

## open_document

| Parameter | Type | Meaning |
| --- | --- | --- |
| `path` | string | File inside an allowed directory |
| `name` | string, optional | Internal document name |

Reads `.FCStd`, STEP, IGES, BREP, STL, OBJ, 3MF, DXF, SVG, AMF. Returns the document summary plus the
FreeCAD type ids found (`Part::Box`, `PartDesign::Pad`, …).

## list_documents / close_document

Bookkeeping only. `close_document` without an argument closes the active document.

## inspect_document

Returns objects with type, visibility, shape metrics and parametric expressions:

```json
{
  "objects": [{
    "name": "Box", "type": "Part::Box", "visible": true,
    "shape": {"volume_mm3": 6000.0, "area_mm2": 2200.0,
              "bbox_mm": [10.0, 20.0, 30.0], "solids": 1, "faces": 6, "edges": 12},
    "expressions": [{"property": "Length", "expression": "Spreadsheet.Width * 2"}]
  }],
  "timeline": ["Box", "Pin"]
}
```

## measure

```json
{
  "objects": [{"name": "Box", "volume_mm3": 6000.0, "area_mm2": 2200.0,
               "center_of_mass_mm": {"x": 5.0, "y": 10.0, "z": 15.0},
               "faces": 6, "edges": 12, "vertices": 8}],
  "total": {"volume_mm3": 6000.0, "area_mm2": 2200.0, "faces": 6, "edges": 12, "vertices": 8},
  "bbox": {"min_mm": [0, 0, 0], "max_mm": [10, 20, 30], "size_mm": [10, 20, 30], "diagonal_mm": 37.4}
}
```

`objects` restricts the measurement to those object names. Overlapping solids are summed as-is;
use a boolean union in FreeCAD first if you need a true union volume.

## bounding_box

Compact form of the bbox block: `size_mm`, `min_mm`, `max_mm`, `center_mm`.

## export_document

| Parameter | Type | Meaning |
| --- | --- | --- |
| `path` | string | Target file, extension decides the format |
| `format` | string, optional | `step`, `iges`, `brep`, `stl`, `obj`, `3mf`, `dxf`, `svg`, `amf`, `fcstd` |
| `document` | string, optional | Source document |

The written file is verified to exist; the response carries its size.

## save_document

Saves to the current path, or to a new `.FCStd` path.

## parameters / set_parameter

`parameters` lists every expression (`Object.Property` → expression) and every spreadsheet alias.

`set_parameter` writes an expression and rebuilds:

```json
{"reference": "Box.Length", "value": "40"}
{"reference": "Width", "value": "Box.Length * 2"}
```

The second form requires `Width` to be a spreadsheet alias.

## recompute

Re-evaluates the document timeline. Useful after `execute_python` touched properties directly.

## execute_python

```json
{"code": "result = [o.Name for o in doc.Objects]"}
```

`doc`, `FreeCAD`/`App` and `result` are in scope. Standard output is captured (last 8 kB).
Errors do not kill the worker; they come back as `freecad_error` with a traceback.
Disable with `--no-execute` or `--read-only`. This is trusted host-code execution, not sandboxed.

## screenshot

| Parameter | Type | Default | Meaning |
| --- | --- | --- | --- |
| `path` | string | — | `.png` or `.svg` target |
| `view` | string | `iso` | `iso`, `front`, `back`, `left`, `right`, `top`, `bottom` |
| `width`, `height` | int | 1200×800 | Pixels, clamped to 200…4096 |
| `document` | string | active | Source document |

Orthographic, flat-shaded projection of the tessellated geometry. No GPU, no X server, no OpenGL.
PNG needs `matplotlib` in FreeCAD's Python; SVG needs nothing and is always available.

## worker_status / restart_worker

`worker_status` reports liveness, PID, versions and the last lines of the worker log.
`restart_worker` replaces the process — unsaved documents are lost.

## Error kinds

| Kind | Meaning |
| --- | --- |
| `worker_unavailable` | freecadcmd missing, crashed or did not connect |
| `timeout` | call exceeded `--timeout`; worker was restarted |
| `unknown_document` / `unknown_object` / `unknown_property` | bad reference |
| `empty_selection` | nothing to measure or render |
| `unsupported_format` | export format not available in this FreeCAD build |
| `freecad_error` | FreeCAD raised; message plus traceback |
| `renderer_unavailable` | PNG dependency missing in FreeCAD embedded Python; use SVG |
| `rejected` | the safety layer refused the request before FreeCAD saw it |
