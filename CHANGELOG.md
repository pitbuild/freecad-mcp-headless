# Changelog

## 0.1.1 — 2026-11-19

- Measure, bounding box and rendering now ignore PartDesign datum geometry (planes,
  lines, points, coordinate systems) and sketches. A real assembly previously reported
  ~1e168 mm³ and a 2e100 mm bounding box because datum planes carry infinite faces.
- PartDesign features (Pad, Pocket, Boolean, Fillet, Pattern, …) are no longer measured
  individually: each one carries its body's current result, so summing them counted the
  same solid many times over. Only the Body is measured unless features are named
  explicitly.
- Regression test for datum geometry; verified against a real 82-object, 4-body
  PartDesign assembly.

## 0.1.0 — 2026-11-19

First public release.

- Persistent `freecadcmd` worker over a token-guarded loopback socket
- Tools: `freecad_info`, `open_document`, `list_documents`, `close_document`, `inspect_document`,
  `measure`, `bounding_box`, `export_document`, `save_document`, `parameters`, `set_parameter`,
  `recompute`, `execute_python`, `screenshot`, `worker_status`, `restart_worker`
- Safety: symlink-resolved path allow-list, per-operation extension allow-lists,
  input size guard (not an output quota),
  call timeouts with worker restart, optional `--no-execute` and tool-level `--read-only` for viewers
- Rendering without a display: flat-shaded PNG via matplotlib, dependency-free SVG writer
- Test suite: 27 unit tests with a protocol-faithful fake worker, 12 integration tests against
  FreeCAD 1.1.4, including real timeout/crash recovery and read-only MCP reads;
  CI matrix for Python 3.10–3.12 plus an integration job on GitHub Actions
