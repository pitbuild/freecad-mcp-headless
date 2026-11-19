import stat
import sys
from pathlib import Path

import pytest

FAKE_WORKER = Path(__file__).with_name("fake_worker.py")


@pytest.fixture()
def fake_freecad(tmp_path: Path) -> str:
    """Create an executable that behaves like freecadcmd (handshake + echo methods)."""
    binary = tmp_path / "freecadcmd"
    binary.write_text(
        f"#!/bin/sh\nexec {sys.executable} {FAKE_WORKER}\n"
    )
    binary.chmod(binary.stat().st_mode | stat.S_IEXEC)
    return str(binary)


@pytest.fixture()
def allowed_dir(tmp_path: Path) -> Path:
    target = tmp_path / "cad"
    target.mkdir()
    return target


@pytest.fixture()
def sample_step(allowed_dir: Path) -> Path:
    path = allowed_dir / "part.step"
    path.write_text("ISO-10303-21;\nEND-ISO-10303-21;\n")
    return path
