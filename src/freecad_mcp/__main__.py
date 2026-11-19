"""Command line entry point for the headless FreeCAD MCP server."""

from __future__ import annotations

import argparse
import logging
import os
import sys

from .bridge import FreeCADWorker, WorkerConfig
from .safety import PathGuard
from .server import FreeCADService, build_server


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="freecad-mcp-headless",
        description="MCP server for headless FreeCAD (no GUI, no X server).",
    )
    parser.add_argument(
        "--allow-path",
        action="append",
        default=[],
        metavar="DIR",
        help="directory the server may read and write (repeatable, default: current directory)",
    )
    parser.add_argument(
        "--freecadcmd",
        default=None,
        metavar="PATH",
        help="path to freecadcmd (default: FREECADCMD, PATH, common install locations)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        default=float(os.environ.get("FREECAD_MCP_TIMEOUT", "120")),
        help="seconds to wait for one FreeCAD call (default: 120)",
    )
    parser.add_argument(
        "--max-file-size",
        type=int,
        default=int(os.environ.get("FREECAD_MCP_MAX_FILE_SIZE", str(256 * 1024**2))),
        help="largest file the server will open, in bytes (default: 256 MiB)",
    )
    parser.add_argument(
        "--read-only",
        action="store_true",
        help="expose only inspection and measurement tools (no model or output writes)",
    )
    parser.add_argument(
        "--no-execute",
        action="store_true",
        help="do not expose the execute_python tool",
    )
    parser.add_argument(
        "--log-level",
        default=os.environ.get("FREECAD_MCP_LOG_LEVEL", "WARNING"),
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    logging.basicConfig(
        level=getattr(logging, args.log_level),
        stream=sys.stderr,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    # stdout belongs to the MCP transport; anything else must go to stderr.
    roots = args.allow_path or [os.getcwd()]
    guard = PathGuard(roots, max_bytes=args.max_file_size)
    worker = FreeCADWorker(WorkerConfig(freecadcmd=args.freecadcmd, timeout=args.timeout))
    service = FreeCADService(
        guard, worker, allow_execute=not args.no_execute, read_only=args.read_only
    )
    server = build_server(service)
    try:
        server.run("stdio")
    finally:
        worker.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
