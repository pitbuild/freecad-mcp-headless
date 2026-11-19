# Changelog

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
