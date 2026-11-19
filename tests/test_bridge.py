import time

import pytest

from freecad_mcp.bridge import (
    FreeCADError,
    FreeCADWorker,
    WorkerConfig,
    WorkerUnavailable,
    find_freecadcmd,
)


def test_find_freecadcmd_rejects_bogus_path():
    with pytest.raises(WorkerUnavailable):
        find_freecadcmd("/nonexistent/freecadcmd")


def test_start_and_call(fake_freecad):
    worker = FreeCADWorker(WorkerConfig(freecadcmd=fake_freecad, timeout=10))
    try:
        hello = worker.start()
        assert hello["freecad"] == "0.0-test"
        assert worker.alive
        result = worker.call("ping")
        assert result["method"] == "ping"
    finally:
        worker.close()
    assert not worker.alive


def test_error_is_structured(fake_freecad):
    worker = FreeCADWorker(WorkerConfig(freecadcmd=fake_freecad, timeout=10))
    try:
        worker.start()
        with pytest.raises(FreeCADError) as excinfo:
            worker.call("explode")
        assert excinfo.value.kind == "worker_error"
        assert "boom" in str(excinfo.value)
        # the worker survives a handled error
        assert worker.call("ping")["method"] == "ping"
    finally:
        worker.close()


def test_timeout_kills_and_restarts(fake_freecad, monkeypatch):
    monkeypatch.setenv("FAKE_DELAY_MS", "1500")
    worker = FreeCADWorker(WorkerConfig(freecadcmd=fake_freecad, timeout=0.3))
    try:
        worker.start()
        first_pid = worker.describe()["pid"]
        with pytest.raises(FreeCADError) as excinfo:
            worker.call("slow")
        assert excinfo.value.kind == "timeout"
        assert not worker.alive
        # next call transparently starts a fresh worker
        monkeypatch.delenv("FAKE_DELAY_MS")
        result = worker.call("ping")
        assert result["method"] == "ping"
        assert worker.describe()["pid"] != first_pid
    finally:
        worker.close()


def test_crash_is_detected(fake_freecad, monkeypatch):
    monkeypatch.setenv("FAKE_EXIT_AFTER", "1")
    worker = FreeCADWorker(WorkerConfig(freecadcmd=fake_freecad, timeout=5))
    try:
        worker.start()
        assert worker.call("ping")["closing"] is True
        with pytest.raises(FreeCADError) as excinfo:
            worker.call("ping")
        assert excinfo.value.kind in {"worker_unavailable", "timeout"}
        assert "exited" in str(excinfo.value) or "broke" in str(excinfo.value)
    finally:
        worker.close()


def test_restart_replaces_process(fake_freecad):
    worker = FreeCADWorker(WorkerConfig(freecadcmd=fake_freecad, timeout=10))
    try:
        worker.start()
        pid = worker.describe()["pid"]
        worker.restart()
        assert worker.describe()["pid"] != pid
        assert worker.alive
    finally:
        worker.close()


def test_many_calls_reuse_one_process(fake_freecad):
    worker = FreeCADWorker(WorkerConfig(freecadcmd=fake_freecad, timeout=10))
    try:
        worker.start()
        pid = worker.describe()["pid"]
        started = time.perf_counter()
        for index in range(100):
            assert worker.call("ping")["calls"] == index + 1
        elapsed = time.perf_counter() - started
        assert worker.describe()["pid"] == pid
        assert elapsed < 30
    finally:
        worker.close()


def test_startup_crash_fails_fast(tmp_path):
    """A worker that dies before connecting must not block for the whole startup timeout."""
    binary = tmp_path / "freecadcmd"
    binary.write_text("#!/bin/sh\necho 'boom: missing library' >&2\nexit 7\n")
    binary.chmod(binary.stat().st_mode | 0o111)
    worker = FreeCADWorker(WorkerConfig(freecadcmd=str(binary), startup_timeout=30, timeout=5))
    started = time.perf_counter()
    with pytest.raises(WorkerUnavailable) as excinfo:
        worker.start()
    elapsed = time.perf_counter() - started
    assert elapsed < 10, f"startup failure took {elapsed:.1f}s"
    assert "exited during startup" in str(excinfo.value)
    assert "boom" in str(excinfo.value)
