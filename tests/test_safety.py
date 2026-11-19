import os

import pytest

from freecad_mcp.safety import PathGuard, SafetyError, format_for


def test_allowed_file_passes(allowed_dir, sample_step):
    guard = PathGuard([str(allowed_dir)])
    assert guard.read_path(str(sample_step)) == sample_step.resolve()


def test_traversal_is_rejected(allowed_dir, tmp_path):
    secret = tmp_path / "secret.step"
    secret.write_text("x")
    guard = PathGuard([str(allowed_dir)])
    with pytest.raises(SafetyError):
        guard.read_path(str(allowed_dir / ".." / "secret.step"))


def test_absolute_outside_is_rejected(allowed_dir):
    guard = PathGuard([str(allowed_dir)])
    with pytest.raises(SafetyError):
        guard.read_path("/etc/passwd")


def test_symlink_escape_is_rejected(allowed_dir, tmp_path):
    outside = tmp_path / "outside.step"
    outside.write_text("x")
    link = allowed_dir / "link.step"
    os.symlink(outside, link)
    guard = PathGuard([str(allowed_dir)])
    with pytest.raises(SafetyError):
        guard.read_path(str(link))


def test_extension_is_enforced(allowed_dir):
    bad = allowed_dir / "script.sh"
    bad.write_text("echo hi")
    guard = PathGuard([str(allowed_dir)])
    with pytest.raises(SafetyError):
        guard.read_path(str(bad))


def test_size_limit(allowed_dir):
    big = allowed_dir / "big.step"
    big.write_bytes(b"0" * 2048)
    guard = PathGuard([str(allowed_dir)], max_bytes=1024)
    with pytest.raises(SafetyError):
        guard.read_path(str(big))


def test_write_requires_existing_parent(allowed_dir):
    guard = PathGuard([str(allowed_dir)])
    with pytest.raises(SafetyError):
        guard.write_path(str(allowed_dir / "missing" / "out.step"))


def test_write_rejects_bad_extension(allowed_dir):
    guard = PathGuard([str(allowed_dir)])
    with pytest.raises(SafetyError):
        guard.write_path(str(allowed_dir / "out.exe"))


def test_png_guard(allowed_dir):
    guard = PathGuard([str(allowed_dir)])
    assert guard.png_path(str(allowed_dir / "shot.png")).suffix == ".png"
    with pytest.raises(SafetyError):
        guard.png_path(str(allowed_dir / "shot.jpg"))


def test_format_inference(allowed_dir):
    fmt, path = format_for(allowed_dir / "part.step", None)
    assert fmt == "step"
    fmt, path = format_for(allowed_dir / "part.dat", "stl")
    assert fmt == "stl"
    assert path.suffix == ".stl"
    with pytest.raises(SafetyError):
        format_for(allowed_dir / "part.unknown", None)


def test_relative_path_resolves_against_cwd(allowed_dir, monkeypatch):
    monkeypatch.chdir(allowed_dir)
    guard = PathGuard([str(allowed_dir)])
    (allowed_dir / "rel.step").write_text("x")
    assert guard.read_path("rel.step").name == "rel.step"


def test_guard_reports_roots(allowed_dir):
    guard = PathGuard([str(allowed_dir)])
    assert str(allowed_dir.resolve()) in guard.describe_roots()


@pytest.mark.parametrize("exists", [True, False])
def test_write_final_symlink_outside_is_rejected(allowed_dir, tmp_path, exists):
    target = tmp_path / "outside.step"
    if exists:
        target.write_text("untouched")
    link = allowed_dir / "link.step"
    link.symlink_to(target)
    with pytest.raises(SafetyError):
        PathGuard([str(allowed_dir)]).write_path(str(link))
    assert target.exists() == exists
    if exists:
        assert target.read_text() == "untouched"


@pytest.mark.parametrize("exists", [True, False])
def test_write_final_internal_symlink_resolves(allowed_dir, exists):
    target = allowed_dir / "target.step"
    if exists:
        target.write_text("old")
    link = allowed_dir / "link.step"
    link.symlink_to(target)
    checked = PathGuard([str(allowed_dir)]).write_path(str(link))
    assert checked == target
    checked.write_text("new")
    assert link.read_text() == "new"
