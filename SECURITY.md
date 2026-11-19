# Security model

This server executes geometry code in a FreeCAD process and can write files. Treat it like a shell
for CAD data, not like a viewer.

## What is enforced

**Path allow-list.** Every path a tool receives is resolved with `Path.resolve()`, i.e. symlinks are
followed *before* the comparison. The server refuses anything outside the directories passed with
`--allow-path` (default: the working directory). `..` traversal and link escapes are covered by tests.

**Extension allow-list.** Reads: `.FCStd`, `.step/.stp`, `.iges/.igs`, `.brep`, `.stl`, `.obj`,
`.3mf`, `.dxf`, `.svg`, `.amf`. Writes additionally allow `.png`, `.csv`, `.txt`, `.json`.

**Size limit.** Files larger than `--max-file-size` (default 256 MiB) are rejected before FreeCAD
opens them. Existing write targets are checked too, but generated output sizes are not capped.

**Timeouts.** A call that exceeds `--timeout` kills the worker and returns a `timeout` error. The next
call starts a fresh worker, so a hanging geometry operation cannot wedge the server.

**Loopback-only transport.** The worker listens on an ephemeral port bound to `127.0.0.1` and only
accepts a connection that presents a 48-character random token generated per process start. The
worker never opens a port that is reachable from the network.

**No network use.** The server makes no outbound requests and sends no telemetry.

**Tool-level read-only mode.** `--read-only` removes save/export/screenshot, parameter changes,
recompute, restart and arbitrary Python from the MCP surface. Inspection, measurement and
open/close session bookkeeping remain available. It is not an OS-level filesystem sandbox.

## What is *not* enforced

- `execute_python` runs arbitrary Python with the rights of the user running the server. That is the
  point of the tool — it is the escape hatch for operations the typed tools do not cover. Disable it
  with `--no-execute` if the client is not fully trusted.
- FreeCAD itself may load Python add-ons from the user profile and execute them. Run the server as a
  dedicated user if that matters to you.
- Path validation and FreeCAD's later file open are separate operations (TOCTOU). A local
  actor able to replace directories/symlinks can race the checks. Use directories not writable
  by untrusted users and OS isolation when needed; this guard is not a sandbox.
- Files written by FreeCAD are ordinary files; permissions follow the process umask.

## Recommended deployment

```bash
# Viewer (no CAD/output writes):
freecad-mcp-headless --allow-path /srv/cad/in --read-only
# Trusted typed-tool editor (writes allowed, arbitrary Python disabled):
freecad-mcp-headless --allow-path /srv/cad/in --allow-path /srv/cad/out --no-execute
```

- one unprivileged user per server instance
- allow-list directories only, never `$HOME` or `/`
- `--read-only` for viewers; `--no-execute` only disables arbitrary Python, not writes
- keep the MCP transport local (stdio). If you proxy it over HTTP, put authentication in front.

## Reporting

Open an issue at <https://github.com/pitbuild/freecad-mcp-headless/issues>. Please do not publish a
working exploit before a fix is available.
