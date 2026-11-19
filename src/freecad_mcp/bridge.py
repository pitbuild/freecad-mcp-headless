"""Bridge between the MCP server and a persistent ``freecadcmd`` worker."""

from __future__ import annotations

import glob
import json
import os
import shutil
import socket
import subprocess
import tempfile
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path

WORKER_SCRIPT = Path(__file__).with_name("worker.py")
DEFAULT_TIMEOUT = 120.0
STARTUP_TIMEOUT = 90.0


class FreeCADError(Exception):
    """Structured error coming back from the worker."""

    def __init__(self, message: str, *, kind: str = "freecad_error", detail: str | None = None) -> None:
        super().__init__(message)
        self.kind = kind
        self.detail = detail


class WorkerUnavailable(FreeCADError):
    def __init__(self, message: str, *, detail: str | None = None) -> None:
        super().__init__(message, kind="worker_unavailable", detail=detail)


def find_freecadcmd(explicit: str | None = None) -> str:
    """Locate ``freecadcmd``.

    Order: explicit argument, ``FREECADCMD`` environment variable, ``PATH``, then
    the usual install locations, including extracted AppImages.
    """
    candidates: list[str] = []
    if explicit:
        expanded = os.path.expanduser(explicit)
        if not (os.path.isfile(expanded) and os.access(expanded, os.X_OK)):
            raise WorkerUnavailable(f"freecadcmd not executable: {expanded}")
        return expanded
    env = os.environ.get("FREECADCMD")
    if env:
        candidates.append(os.path.expanduser(env))
    found = shutil.which("freecadcmd") or shutil.which("FreeCADCmd")
    if found:
        candidates.append(found)
    candidates += [
        "/usr/bin/freecadcmd",
        "/usr/local/bin/freecadcmd",
        "/usr/lib/freecad/bin/freecadcmd",
        "/usr/lib/freecad-daily/bin/freecadcmd",
        "/opt/freecad/bin/freecadcmd",
        "/Applications/FreeCAD.app/Contents/Resources/bin/freecadcmd",
    ]
    for pattern in (
        "/opt/agent-tools/freecad/*/usr/bin/freecadcmd",
        "/opt/freecad*/bin/freecadcmd",
        "/snap/freecad/current/usr/bin/freecadcmd",
    ):
        candidates += sorted(glob.glob(pattern))

    for candidate in candidates:
        if candidate and os.path.isfile(candidate) and os.access(candidate, os.X_OK):
            return candidate
    raise WorkerUnavailable(
        "freecadcmd not found. Install FreeCAD, put freecadcmd on PATH, "
        "or set FREECADCMD=/path/to/freecadcmd."
    )


@dataclass
class WorkerConfig:
    freecadcmd: str | None = None
    timeout: float = DEFAULT_TIMEOUT
    startup_timeout: float = STARTUP_TIMEOUT
    env: dict[str, str] = field(default_factory=dict)


class FreeCADWorker:
    """A long-lived FreeCAD process addressed over a token-guarded socket."""

    def __init__(self, config: WorkerConfig | None = None) -> None:
        self.config = config or WorkerConfig()
        self._lock = threading.Lock()
        self._proc: subprocess.Popen | None = None
        self._socket: socket.socket | None = None
        self._stream = None
        self._next_id = 1
        self._stderr_path: Path | None = None
        self._log_dir: Path | None = None
        self.hello: dict | None = None

    # -- lifecycle -------------------------------------------------------
    @property
    def alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None and self._socket is not None

    def start(self) -> dict:
        with self._lock:
            if self.alive:
                return self.hello or {}
            self._start_locked()
            return self.hello or {}

    def _start_locked(self) -> None:
        self._teardown_locked()
        binary = find_freecadcmd(self.config.freecadcmd)

        listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        listener.settimeout(self.config.startup_timeout)
        port = listener.getsockname()[1]

        token = os.urandom(24).hex()
        self._log_dir = Path(tempfile.mkdtemp(prefix="freecad-mcp-"))
        self._stderr_path = self._log_dir / "worker.log"

        env = dict(os.environ)
        env.update(self.config.env)
        env["FREECAD_MCP_PORT"] = str(port)
        env["FREECAD_MCP_TOKEN"] = token
        env.setdefault("PYTHONUNBUFFERED", "1")
        # Keep FreeCAD from asking anything interactively.
        env.setdefault("FREECAD_USER_HOME", str(self._log_dir / "user-home"))

        stderr = open(self._stderr_path, "wb")
        try:
            self._proc = subprocess.Popen(
                [binary, str(WORKER_SCRIPT)],
                stdin=subprocess.DEVNULL,
                stdout=stderr,
                stderr=stderr,
                env=env,
                cwd=str(self._log_dir),
                start_new_session=True,
            )
        except OSError as exc:
            listener.close()
            raise WorkerUnavailable(f"could not start {binary}: {exc}") from exc
        finally:
            stderr.close()

        # Wait for the worker, but fail fast if the process dies on startup instead of
        # blocking for the whole startup timeout.
        deadline = time.monotonic() + self.config.startup_timeout
        conn = None
        try:
            while conn is None:
                if self._proc is not None and self._proc.poll() is not None:
                    code = self._proc.returncode
                    log = self.last_log()
                    self._teardown_locked()
                    raise WorkerUnavailable(
                        f"FreeCAD exited during startup (code {code}). Worker log:\n{log}"
                    )
                remaining = deadline - time.monotonic()
                if remaining <= 0:
                    log = self.last_log()
                    self._teardown_locked()
                    raise WorkerUnavailable(
                        f"FreeCAD did not connect back in time. Worker log:\n{log}"
                    )
                listener.settimeout(min(0.25, remaining))
                try:
                    conn, _ = listener.accept()
                except TimeoutError:
                    continue
        finally:
            try:
                listener.close()
            except OSError:
                pass
        if conn is None:  # pragma: no cover - defensive
            raise WorkerUnavailable("FreeCAD did not connect back in time")

        conn.settimeout(self.config.startup_timeout)
        stream = conn.makefile("rwb")
        try:
            line = stream.readline()
        except OSError as exc:
            self._teardown_locked()
            raise WorkerUnavailable(f"worker handshake failed: {exc}") from exc
        if not line:
            self._teardown_locked()
            raise WorkerUnavailable("worker exited during handshake:\n" + self.last_log())
        try:
            hello = json.loads(line.decode("utf-8"))
        except json.JSONDecodeError as exc:
            self._teardown_locked()
            raise WorkerUnavailable(f"worker sent invalid handshake: {line[:400]!r}") from exc
        if hello.get("token") != token:
            self._teardown_locked()
            raise WorkerUnavailable("worker handshake carried the wrong token")
        if hello.get("pid") != self._proc.pid:
            # The socket is private to this process tree, but a mismatch still
            # signals something we do not understand.
            self._teardown_locked()
            raise WorkerUnavailable("worker handshake pid does not match the spawned process")

        conn.settimeout(self.config.timeout)
        self._socket = conn
        self._stream = stream
        self.hello = hello

    def call(self, method: str, params: dict | None = None, *, timeout: float | None = None) -> dict:
        effective = timeout or self.config.timeout
        with self._lock:
            if not self.alive:
                self._start_locked()
            assert self._socket is not None and self._stream is not None
            request_id = self._next_id
            self._next_id += 1
            payload = json.dumps({"id": request_id, "method": method, "params": params or {}})
            try:
                self._socket.settimeout(effective)
                self._stream.write((payload + "\n").encode("utf-8"))
                self._stream.flush()
                line = self._stream.readline()
            except TimeoutError as exc:
                self._teardown_locked()
                raise FreeCADError(
                    f"FreeCAD did not answer {method!r} within {effective:g}s; the worker was restarted",
                    kind="timeout",
                    detail=self.last_log(),
                ) from exc
            except OSError as exc:
                self._teardown_locked()
                raise WorkerUnavailable(
                    f"the connection to FreeCAD broke during {method!r}: {exc}\n" + self.last_log()
                ) from exc
            if not line:
                self._teardown_locked()
                raise WorkerUnavailable(
                    f"FreeCAD exited during {method!r}. Worker log:\n" + self.last_log()
                )
            try:
                response = json.loads(line.decode("utf-8"))
            except json.JSONDecodeError as exc:
                raise FreeCADError(
                    f"FreeCAD sent an unreadable reply: {line[:400]!r}", kind="protocol"
                ) from exc
            if response.get("id") != request_id:
                raise FreeCADError("FreeCAD replied out of order", kind="protocol")
            if not response.get("ok"):
                error = response.get("error") or {}
                raise FreeCADError(
                    error.get("message") or "unknown FreeCAD error",
                    kind=error.get("kind") or "freecad_error",
                    detail=error.get("traceback"),
                )
            return response.get("result") or {}

    def restart(self) -> dict:
        with self._lock:
            self._teardown_locked()
            self._start_locked()
            return self.hello or {}

    def close(self) -> None:
        with self._lock:
            if self.alive:
                try:
                    self._socket.settimeout(5.0)
                    self._stream.write((json.dumps({"id": 0, "method": "shutdown"}) + "\n").encode())
                    self._stream.flush()
                    self._stream.readline()
                except OSError:
                    pass
            self._teardown_locked()

    def _teardown_locked(self) -> None:
        proc = self._proc
        self._proc = None
        try:
            if self._socket is not None:
                self._socket.close()
        except OSError:
            pass
        self._socket = None
        self._stream = None
        if proc is not None and proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=10)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except subprocess.TimeoutExpired:
                    pass

    # -- diagnostics -----------------------------------------------------
    def last_log(self, lines: int = 40) -> str:
        if not self._stderr_path or not self._stderr_path.exists():
            return "(no worker log)"
        try:
            text = self._stderr_path.read_text(errors="replace").strip().splitlines()
        except OSError as exc:
            return f"(worker log unreadable: {exc})"
        return "\n".join(text[-lines:]) or "(worker log empty)"

    def describe(self) -> dict:
        return {
            "alive": self.alive,
            "pid": self._proc.pid if self._proc else None,
            "freecad": (self.hello or {}).get("freecad"),
            "python": (self.hello or {}).get("python"),
            "protocol": (self.hello or {}).get("protocol"),
            "uptime_s": None,
        }

    def __enter__(self) -> FreeCADWorker:
        self.start()
        return self

    def __exit__(self, *exc_info) -> None:
        self.close()


def timed(fn, *args, **kwargs):
    started = time.perf_counter()
    result = fn(*args, **kwargs)
    return result, time.perf_counter() - started
