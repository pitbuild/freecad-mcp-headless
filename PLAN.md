# Build plan

Target: a production-usable MCP server for headless FreeCAD, published as a public repository.

## Sections

1. **Worker bridge** — `freecadcmd` process, loopback TCP, token handshake, JSON line protocol,
   call IDs, timeouts, crash detection and restart. Acceptance: a worker survives 100 calls, a
   killed worker is detected within one call and replaced, a hung call times out without leaking a process.
2. **Safety layer** — path allow-list with symlink resolution, extension allow-lists, size limits,
   tool-level read-only mode. Acceptance: traversal, symlink escape and oversized files are rejected
   with structured errors; allowed paths pass.
3. **Worker methods** — open/save/close documents, inspect, measure, bounding box, export, parameters,
   execute, screenshot. Acceptance: every method returns JSON-serialisable data and raises structured
   errors instead of crashing the worker.
4. **MCP surface** — stdio server with typed tools, docstrings with units, structured error text.
   Acceptance: `mcp` client handshake succeeds, tools list is complete, each tool round-trips.
5. **Tests** — unit tests without FreeCAD, integration tests with it. Acceptance: `pytest` green in
   both modes, coverage of each tool and each guard.
6. **Docs and packaging** — README, `docs/TOOLS.md`, `SECURITY.md`, pyproject extras, CI on GitHub
   Actions. Acceptance: fresh clone installs and runs the documented commands.
7. **Live proof** — register the server in mcporter, drive a real `.FCStd` through
   open → measure → export STEP → screenshot, and cross-check the STEP in a second CAD engine.

## Ground rules

- MIT, public repository (explicit owner decision; this project is not client work).
- No network access at runtime, no telemetry, no external services.
- Every tool call is path-guarded before it reaches FreeCAD.
