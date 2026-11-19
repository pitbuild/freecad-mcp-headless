# freecad-mcp-headless

An MCP (Model Context Protocol) server for **headless FreeCAD** — no GUI, no X server, no RPC add-on.

It drives the official `freecadcmd` binary through a persistent worker process, so a document is opened
once and stays open across tool calls. Everything FreeCAD can script, this server can expose:
`.FCStd` documents, STEP/IGES import and export, measurements, parameter changes, feature trees,
mesh export and PNG renders.

```
MCP client  <--stdio-->  freecad-mcp-headless  <--TCP-->  freecadcmd worker (persistent)
```

## Why

FreeCAD ships a full Python API but no MCP server. The community MCP projects require a running
FreeCAD **GUI** with an RPC socket, which rules out servers and containers. This project has no GUI
dependency at all: it starts `freecadcmd`, keeps one worker alive, and speaks a small JSON protocol
over loopback TCP.

## Status

Early but functional. See `PLAN.md` for the build plan and `docs/TOOLS.md` for the tool reference.

## Quick start

```bash
pip install -e .
freecad-mcp-headless --allow-path ~/cad --allow-path /tmp
```

MCP client configuration (mcporter / Claude Desktop / any stdio MCP client):

```json
{
  "mcpServers": {
    "freecad": {
      "command": "freecad-mcp-headless",
      "args": ["--allow-path", "/root/cad-work"]
    }
  }
}
```

## Tools

| Tool | What it does |
| --- | --- |
| `freecad_info` | Version, Python version, worker PID, loaded documents |
| `open_document` | Open `.FCStd`, `.step`, `.stp`, `.iges`, `.igs`, `.brep`, `.stl`, `.obj` |
| `list_documents` / `close_document` | Session bookkeeping |
| `inspect_document` | Objects, types, visibility, key properties, feature timeline |
| `measure` | Volume, surface area, bounding box, centre of mass, face/edge/vertex counts |
| `bounding_box` | Compact size report in mm |
| `export_document` | STEP, IGES, BREP, STL, OBJ, 3MF, DXF, SVG |
| `save_document` | Save as `.FCStd`, optionally with a copy |
| `parameters` / `set_parameter` | Read and drive FreeCAD expressions, then rebuild |
| `execute_python` | Run a snippet inside the worker (guarded, optional) |
| `screenshot` | Flat-shaded PNG render without a GPU or display |
| `worker_status` / `restart_worker` | Health and recovery |

Full parameter documentation: `docs/TOOLS.md`.

## Safety

- **Path allow-list**: every path argument must resolve inside a directory passed with `--allow-path`.
  Symlinks are resolved first, so `../` and link escapes are rejected.
- **Extension allow-list** per operation.
- **File size limit** (`--max-file-size`, default 256 MiB).
- **Per-call timeout** (`--timeout`, default 120 s); a hung call restarts the worker.
- **`--no-execute`** removes the `execute_python` tool entirely.
- The worker never listens on a public interface: the TCP socket binds `127.0.0.1` on an ephemeral
  port and requires a random token in the handshake.

Read `SECURITY.md` before exposing the server to an untrusted client.

## Requirements

- Python 3.10+
- FreeCAD 0.20+ with `freecadcmd` on `PATH`, or set `FREECADCMD=/path/to/freecadcmd`
- `matplotlib` for `screenshot` (optional dependency: `pip install -e .[render]`)

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,render]"
pytest -m "not integration"     # unit tests, no FreeCAD needed
pytest -m integration           # needs freecadcmd
```

## License

MIT
