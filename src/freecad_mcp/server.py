"""MCP surface: thin, typed tools over the FreeCAD worker."""

from __future__ import annotations

from mcp.server.fastmcp import FastMCP

from .bridge import FreeCADError, FreeCADWorker
from .safety import PathGuard, SafetyError, format_for

try:  # older and newer SDKs expose the error class in different places
    from mcp.server.fastmcp.exceptions import ToolError  # type: ignore
except Exception:  # pragma: no cover
    class ToolError(RuntimeError):  # type: ignore
        pass


class FreeCADService:
    """Holds the guard, the worker and the execution policy."""

    def __init__(
        self,
        guard: PathGuard,
        worker: FreeCADWorker | None = None,
        *,
        allow_execute: bool = True,
        read_only: bool = False,
    ) -> None:
        self.guard = guard
        self.worker = worker or FreeCADWorker()
        self.read_only = read_only
        self.allow_execute = allow_execute and not read_only

    # -- helpers ---------------------------------------------------------
    def call(self, method: str, params: dict | None = None, *, timeout: float | None = None) -> dict:
        if self.read_only and method not in {
            "info", "open", "close", "list_documents", "inspect", "measure",
            "bounding_box", "parameters",
        }:
            raise ToolError("rejected: tool is disabled in read-only mode")
        try:
            return self.worker.call(method, params or {}, timeout=timeout)
        except SafetyError as exc:
            raise ToolError(f"rejected: {exc}") from exc
        except FreeCADError as exc:
            message = f"{exc.kind}: {exc}"
            if exc.detail:
                message += f"\n\n{exc.detail}"
            raise ToolError(message) from exc


def build_server(service: FreeCADService, *, name: str = "freecad-headless") -> FastMCP:
    server = FastMCP(name)

    @server.tool()
    def freecad_info() -> dict:
        """Report the FreeCAD build, worker process and open documents.

        Starts the worker on the first call. Use this to verify a working install.
        """
        return service.call("info")

    @server.tool()
    def open_document(path: str, name: str | None = None) -> dict:
        """Open a CAD file and make it the active document.

        Supports .FCStd, .step/.stp, .iges/.igs, .brep, .stl, .obj, .3mf, .dxf, .svg, .amf.
        The file must live inside an allowed directory. Returns the document name,
        object count and the FreeCAD types it contains.
        """
        allowed = service.guard.read_path(path)
        params = {"path": str(allowed)}
        if name:
            params["name"] = name
        return service.call("open", params)

    @server.tool()
    def list_documents() -> dict:
        """List every document in the worker with its file path and modification state."""
        return service.call("list_documents")

    @server.tool()
    def close_document(document: str | None = None) -> dict:
        """Close a document; without an argument the active document is closed."""
        return service.call("close", {"document": document})

    @server.tool()
    def inspect_document(document: str | None = None) -> dict:
        """Describe a document: objects, types, key properties, expressions and timeline order.

        Each object reports volume, area, bounding box, solid/face/edge counts and
        any parametric expressions that drive it.
        """
        return service.call("inspect", {"document": document})

    @server.tool()
    def measure(document: str | None = None, objects: list[str] | None = None) -> dict:
        """Measure geometry in millimetres.

        Returns volume (mm3), surface area (mm2), centre of mass, bounding box,
        diagonal and face/edge/vertex counts — per object and summed. Pass
        ``objects`` to restrict the measurement to selected object names.
        """
        return service.call("measure", {"document": document, "objects": objects})

    @server.tool()
    def bounding_box(document: str | None = None) -> dict:
        """Return the bounding box size, minimum, maximum and centre in millimetres."""
        return service.call("bounding_box", {"document": document})

    @server.tool()
    def export_document(path: str, document: str | None = None, format: str | None = None) -> dict:
        """Export the document geometry to STEP, IGES, BREP, STL, OBJ, 3MF, DXF, SVG, AMF or FCStd.

        The format is inferred from the file extension unless ``format`` is given.
        Returns the written path, format and byte size.
        """
        target = service.guard.write_path(path)
        fmt, target = format_for(target, format)
        target = service.guard.write_path(str(target))
        return service.call(
            "export", {"document": document, "path": str(target), "format": fmt}
        )

    @server.tool()
    def save_document(path: str | None = None, document: str | None = None) -> dict:
        """Save the document to disk, optionally to a new .FCStd path."""
        target = None
        if path:
            target = str(service.guard.write_path(path, extensions={".fcstd", ".fcstd1"}))
        return service.call("save", {"document": document, "path": target})

    @server.tool()
    def parameters(document: str | None = None) -> dict:
        """List every parametric expression and spreadsheet alias in the document."""
        return service.call("parameters", {"document": document})

    @server.tool()
    def set_parameter(reference: str, value: str, document: str | None = None) -> dict:
        """Set a parameter and rebuild the model.

        ``reference`` is either ``Object.Property`` (for example ``Box.Length``) or a
        spreadsheet alias. ``value`` is a number or a FreeCAD expression such as
        ``40 mm`` or ``Box.Length * 2``.
        """
        return service.call(
            "set_parameter", {"document": document, "reference": reference, "value": value}
        )

    @server.tool()
    def recompute(document: str | None = None) -> dict:
        """Re-evaluate the document timeline after external changes."""
        return service.call("recompute", {"document": document})

    if service.allow_execute:

        @server.tool()
        def execute_python(code: str, document: str | None = None) -> dict:
            """Run Python inside the FreeCAD worker and return stdout plus ``result``.

            The document is available as ``doc``, the FreeCAD module as ``FreeCAD``
            (alias ``App``). Assign to a variable named ``result`` to return data.
            This tool is the escape hatch for anything the other tools do not cover;
            it can be disabled on the server with ``--no-execute``.
            """
            return service.call("execute", {"document": document, "code": code})

    @server.tool()
    def screenshot(
        path: str,
        document: str | None = None,
        view: str = "iso",
        width: int = 1200,
        height: int = 800,
    ) -> dict:
        """Render the model to a PNG (or SVG) without a display or GPU.

        Views: iso, front, back, left, right, top, bottom. The image is an orthographic,
        flat-shaded projection of the tessellated geometry, annotated with the
        bounding-box size in millimetres.
        """
        if path.lower().endswith(".png"):
            target = service.guard.png_path(path)
        else:
            target = service.guard.write_path(path)
        return service.call(
            "screenshot",
            {
                "document": document,
                "path": str(target),
                "view": view,
                "width": max(200, min(4096, int(width))),
                "height": max(200, min(4096, int(height))),
            },
        )

    @server.tool()
    def worker_status() -> dict:
        """Report worker health, PID, FreeCAD version and the tail of its log."""
        return {
            "worker": service.worker.describe(),
            "allowed_directories": service.guard.describe_roots(),
            "execute_enabled": service.allow_execute,
            "read_only": service.read_only,
            "log_tail": service.worker.last_log(15),
        }

    @server.tool()
    def restart_worker() -> dict:
        """Restart the FreeCAD process. Open documents are lost unless saved."""
        hello = service.worker.restart()
        return {"restarted": True, "hello": hello}

    if service.read_only:
        for tool in (
            "export_document", "save_document", "set_parameter", "recompute",
            "screenshot", "restart_worker",
        ):
            server.remove_tool(tool)

    return server
