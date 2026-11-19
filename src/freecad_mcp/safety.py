"""Path and input guards for every file the MCP server touches.

All checks happen before a path reaches FreeCAD. Symlinks are resolved first, so
``..`` traversal and link escapes cannot widen the allow-list.
"""

from __future__ import annotations

import os
from pathlib import Path

READ_EXTENSIONS = {
    ".fcstd",
    ".fcstd1",
    ".step",
    ".stp",
    ".iges",
    ".igs",
    ".brep",
    ".stl",
    ".obj",
    ".3mf",
    ".dxf",
    ".svg",
    ".amf",
}

WRITE_EXTENSIONS = READ_EXTENSIONS | {".png", ".csv", ".txt", ".json"}

EXPORT_FORMATS = {
    "step": ".step",
    "stp": ".step",
    "iges": ".iges",
    "igs": ".iges",
    "brep": ".brep",
    "stl": ".stl",
    "obj": ".obj",
    "3mf": ".3mf",
    "dxf": ".dxf",
    "svg": ".svg",
    "amf": ".amf",
    "fcstd": ".FCStd",
}


class SafetyError(Exception):
    """Raised when a request violates a guard. Never reaches FreeCAD."""

    def __init__(self, message: str, *, path: str | None = None) -> None:
        super().__init__(message)
        self.path = path


class PathGuard:
    """Allow-list based file guard.

    Parameters
    ----------
    roots:
        Directories a tool may read from and write to. A relative argument is
        resolved against the current working directory of the server process.
    max_bytes:
        Upper bound for any file that is opened.
    """

    def __init__(self, roots: list[str] | None = None, max_bytes: int = 256 * 1024**2) -> None:
        resolved = [Path(os.path.abspath(os.path.expanduser(r))) for r in (roots or ["."])]
        # Compare canonical paths only, including for symlinked allow-path roots.
        self.roots: list[Path] = []
        for root in resolved:
            self.roots.append(root.resolve())
        self.max_bytes = max_bytes

    # -- helpers ---------------------------------------------------------
    def _inside(self, path: Path) -> bool:
        for root in self.roots:
            try:
                path.relative_to(root)
            except ValueError:
                continue
            return True
        return False

    def describe_roots(self) -> list[str]:
        return [str(r) for r in self.roots]

    def _resolve(self, value: str, *, must_exist: bool) -> Path:
        if not isinstance(value, str) or not value.strip():
            raise SafetyError("path must be a non-empty string")
        raw = Path(os.path.expanduser(value))
        if not raw.is_absolute():
            raw = Path.cwd() / raw
        try:
            if must_exist:
                path = raw.resolve(strict=True)
            else:
                # Resolve the final component too, including dangling symlinks.
                # The eventual target parent must exist; no directories are created.
                path = raw.resolve(strict=False)
                path.parent.resolve(strict=True)
        except (OSError, RuntimeError) as exc:
            raise SafetyError(f"cannot resolve path: {exc}", path=value) from exc
        if not self._inside(path):
            raise SafetyError(
                "path is outside the allowed directories: "
                f"{path} (allowed: {', '.join(self.describe_roots())})",
                path=value,
            )
        return path

    def _check_extension(self, path: Path, allowed: set[str]) -> None:
        suffix = path.suffix.lower()
        if suffix not in allowed:
            raise SafetyError(
                f"file extension {suffix or '(none)'} is not allowed here; "
                f"expected one of: {', '.join(sorted(allowed))}",
                path=str(path),
            )

    def _check_size(self, path: Path) -> None:
        try:
            size = path.stat().st_size
        except OSError as exc:
            raise SafetyError(f"cannot stat file: {exc}", path=str(path)) from exc
        if size > self.max_bytes:
            raise SafetyError(
                f"file is {size} bytes, larger than the limit of {self.max_bytes} bytes",
                path=str(path),
            )

    # -- public API ------------------------------------------------------
    def read_path(self, value: str, *, extensions: set[str] | None = None) -> Path:
        path = self._resolve(value, must_exist=True)
        if not path.is_file():
            raise SafetyError(f"not a regular file: {path}", path=value)
        self._check_extension(path, extensions or READ_EXTENSIONS)
        self._check_size(path)
        return path

    def write_path(self, value: str, *, extensions: set[str] | None = None) -> Path:
        path = self._resolve(value, must_exist=False)
        self._check_extension(path, extensions or WRITE_EXTENSIONS)
        if path.exists():
            if not path.is_file():
                raise SafetyError(f"target exists and is not a regular file: {path}", path=value)
            self._check_size(path)
        if not path.parent.is_dir():
            raise SafetyError(f"target directory does not exist: {path.parent}", path=value)
        return path

    def png_path(self, value: str) -> Path:
        return self.write_path(value, extensions={".png"})


def format_for(path: Path, requested: str | None) -> tuple[str, Path]:
    """Map a requested format name to an export extension.

    Returns ``(format, path)`` where ``path`` carries the matching suffix.
    """
    if requested:
        key = requested.lower().lstrip(".")
        if key not in EXPORT_FORMATS:
            raise SafetyError(
                f"unknown export format {requested!r}; "
                f"expected one of: {', '.join(sorted(EXPORT_FORMATS))}"
            )
        wanted = EXPORT_FORMATS[key]
        if path.suffix.lower() != wanted.lower():
            path = path.with_suffix(wanted)
        return key, path
    suffix = path.suffix.lower()
    if suffix in {".step", ".stp"}:
        return "step", path
    if suffix in {".iges", ".igs"}:
        return "iges", path
    if suffix == ".fcstd":
        return "fcstd", path
    if suffix and suffix[1:] in EXPORT_FORMATS:
        return suffix[1:], path
    raise SafetyError(
        f"cannot infer export format from {path.name!r}; pass an explicit format",
        path=str(path),
    )
