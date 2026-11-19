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

Working and tested against FreeCAD 1.1.4 on Linux, plus a CI job that runs the integration suite on
every push. See `PLAN.md` for the build plan and `docs/TOOLS.md` for the tool reference.

![Example render](docs/example-render.png)

*`screenshot` output for a 10 × 20 × 30 mm block: orthographic, flat-shaded, annotated — produced
without a display, GPU or OpenGL.*

## How it works

1. The server binds a TCP port on `127.0.0.1`, generates a one-time token and starts `freecadcmd`.
2. The worker connects back, proves the token and then serves JSON lines: one request, one reply.
3. Documents stay open in that worker, so `open_document` → `measure` → `export_document` costs one
   FreeCAD startup, not three.
4. A call that exceeds `--timeout` kills the worker; the next call starts a new one. A crash is
   detected the same way, and the log tail is attached to the error.

Because FreeCAD's console build has no OpenGL, `screenshot` renders the tessellated geometry in
software (matplotlib for PNG, a built-in writer for SVG). That is also why the tool never needs an
X server.

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
| `export_document` | STEP, IGES, BREP, STL, OBJ, 3MF, DXF, SVG, AMF |
| `save_document` | Save as `.FCStd`, optionally with a copy |
| `parameters` / `set_parameter` | Read and drive FreeCAD expressions, then rebuild |
| `recompute` | Re-evaluate the document timeline |
| `execute_python` | Run trusted host Python inside the worker (optional, not sandboxed) |
| `screenshot` | Flat-shaded PNG or dependency-free SVG without a GPU or display |
| `worker_status` / `restart_worker` | Health and recovery |

Full parameter documentation: `docs/TOOLS.md`.

## Safety

- **Path allow-list**: every path argument must resolve inside a directory passed with `--allow-path`.
  Symlinks are resolved first, so `../` and link escapes are rejected.
- **Extension allow-list** per operation.
- **Input file size guard** (`--max-file-size`, default 256 MiB); also checks existing write targets,
  but is not a generated-output quota.
- **Per-call timeout** (`--timeout`, default 120 s); a hung call restarts the worker.
- **`--read-only`** exposes inspection/measurement and open/close session bookkeeping only; removes
  save, export, screenshot (both write files), parameter changes, recompute, restart and arbitrary Python.
- **`--no-execute`** removes the `execute_python` tool entirely.
- The worker never listens on a public interface: the TCP socket binds `127.0.0.1` on an ephemeral
  port and requires a random token in the handshake.

Read `SECURITY.md` before exposing the server to an untrusted client.

## Notes for MCP clients

A stdio MCP client keeps one server process alive for the whole session — that is the mode this
server is designed for, and documents stay open between calls.

One-shot CLI wrappers (`mcporter call` and friends) start a fresh process per call unless they run a
daemon, so the document list is empty on every invocation there. Call `open_document` and the
follow-up tool in the same session, or run the whole job through `execute_python` in a single call.

## Troubleshooting

| Symptom | Cause | Fix |
| --- | --- | --- |
| `worker_unavailable: FreeCAD did not connect back in time` | wrong binary, missing libs | run `freecadcmd --version`; check the log tail in the error |
| `rejected: file extension ...` | guard refused the format | see the allowed lists in `SECURITY.md` |
| `renderer_unavailable` | no matplotlib in FreeCAD's Python | write `.svg` instead, or install matplotlib for that interpreter |
| `unknown_document` | FreeCAD rewrites file stems (`fc-smoke` → `fc_smoke`) | pass the name from the `open_document` reply, or the label |
| STEP opens as an empty document | a mesh-only STL was opened as a solid | use `screenshot`/`measure` on meshes, not `export` to STEP |

## Requirements

- Python 3.10+
- FreeCAD 0.20+ with `freecadcmd` on `PATH`, or set `FREECADCMD=/path/to/freecadcmd`
- PNG screenshots need `matplotlib` importable by **FreeCAD's embedded Python**.
  `pip install -e ".[render]"` only installs it into the server environment, which the worker
  does **not** use. For a distro FreeCAD using system Python, install the matching distro package
  (for example `sudo apt install python3-matplotlib` on Debian/Ubuntu). Bundled/AppImage builds
  need a compatible package installed for their own embedded Python; system or server-venv
  packages may not be ABI-compatible. Check `freecad_info` for the worker Python version and
  validate by calling `screenshot` with a `.png` target. If unavailable, it returns
  `renderer_unavailable`: use a `.svg` target, whose built-in renderer needs no optional packages.

## Development

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev,render]"
pytest -m "not integration"     # unit tests, no FreeCAD needed
pytest -m integration           # needs freecadcmd
```

## License

MIT
