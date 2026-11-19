"""Headless FreeCAD MCP server."""

from importlib.metadata import PackageNotFoundError, version

try:  # single source of truth is pyproject.toml
    __version__ = version("freecad-mcp-headless")
except PackageNotFoundError:  # running from a source tree without installation
    __version__ = "0.0.0+source"

__all__ = ["__version__"]
