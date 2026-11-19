"""Integration tests against a real freecadcmd. Run with ``pytest -m integration``."""

import math

import pytest

from freecad_mcp.bridge import FreeCADError, FreeCADWorker, WorkerConfig

pytestmark = pytest.mark.integration


@pytest.fixture(scope="module")
def worker():
    instance = FreeCADWorker(WorkerConfig(timeout=180))
    instance.start()
    yield instance
    instance.close()


@pytest.fixture(scope="module")
def model(worker, tmp_path_factory):
    workdir = tmp_path_factory.mktemp("fc")
    doc_path = workdir / "block.FCStd"
    code = f"""
import FreeCAD, Part
doc = FreeCAD.newDocument("Block")
box = doc.addObject("Part::Box", "Block")
box.Length, box.Width, box.Height = 10.0, 20.0, 30.0
cyl = doc.addObject("Part::Cylinder", "Pin")
cyl.Radius, cyl.Height = 3.0, 25.0
cyl.Placement.Base = FreeCAD.Vector(5.0, 10.0, 10.0)
doc.recompute()
doc.saveAs({str(doc_path)!r})
result = doc.Name
"""
    created = worker.call("execute", {"code": code})
    assert created["result"] == "Block"
    return doc_path


def test_info_reports_freecad(worker):
    info = worker.call("info")
    assert info["freecad"]["version"].count(".") >= 1
    assert info["worker"]["protocol"] == 1


def test_open_and_measure(worker, model):
    opened = worker.call("open", {"path": str(model)})
    assert opened["name"] == "Block"
    measured = worker.call("measure", {"document": "Block"})
    box = 10 * 20 * 30
    pin = math.pi * 3**2 * 25
    assert measured["total"]["volume_mm3"] == pytest.approx(box + pin, rel=1e-3)
    assert measured["bbox"]["size_mm"] == pytest.approx([10.0, 20.0, 35.0], rel=1e-6)
    assert measured["total"]["faces"] >= 6


def test_inspect_lists_objects(worker, model):
    inspected = worker.call("inspect", {"document": "Block"})
    names = {obj["name"] for obj in inspected["objects"]}
    assert {"Block", "Pin"} <= names
    assert inspected["timeline"] == ["Block", "Pin"]


def test_export_step_roundtrip(worker, model, tmp_path):
    target = tmp_path / "block.step"
    exported = worker.call(
        "export", {"document": "Block", "path": str(target), "format": "step"}
    )
    assert exported["bytes"] > 1000
    reopened = worker.call("open", {"path": str(target)})
    assert reopened["objects"] >= 1
    measured = worker.call("measure", {"document": reopened["name"]})
    assert measured["total"]["volume_mm3"] == pytest.approx(
        worker.call("measure", {"document": "Block"})["total"]["volume_mm3"], rel=1e-4
    )
    worker.call("close", {"document": reopened["name"]})


def test_screenshot_png_and_svg(worker, model, tmp_path):
    png = tmp_path / "shot.png"
    result = worker.call("screenshot", {"document": "Block", "path": str(png), "width": 640, "height": 480})
    assert result["triangles"] > 10
    assert png.stat().st_size > 5000
    assert png.read_bytes()[:8] == b"\x89PNG\r\n\x1a\n"

    svg = tmp_path / "shot.svg"
    worker.call("screenshot", {"document": "Block", "path": str(svg), "view": "front"})
    text = svg.read_text()
    assert text.startswith("<svg") and "<polygon" in text


def test_parameter_roundtrip(worker, tmp_path):
    path = tmp_path / "param.FCStd"
    code = f"""
import FreeCAD
doc = FreeCAD.newDocument("Param")
box = doc.addObject("Part::Box", "Box")
box.Length = 10.0
doc.recompute()
doc.saveAs({str(path)!r})
result = doc.Name
"""
    worker.call("execute", {"code": code})
    listed = worker.call("parameters", {"document": "Param"})
    assert listed["document"] == "Param"
    worker.call("set_parameter", {"document": "Param", "reference": "Box.Length", "value": "40"})
    measured = worker.call("measure", {"document": "Param"})
    assert measured["total"]["volume_mm3"] == pytest.approx(10 * 10 * 40, rel=1e-3)


def test_unknown_document_is_structured(worker):
    with pytest.raises(FreeCADError) as excinfo:
        worker.call("measure", {"document": "does-not-exist"})
    assert excinfo.value.kind == "unknown_document"


def test_worker_survives_bad_python(worker, model):
    worker.call("open", {"path": str(model)})
    with pytest.raises(FreeCADError):
        worker.call("execute", {"document": "Block", "code": "raise ValueError('nope')"})
    assert worker.call("ping")["documents"]


def test_step_import_does_not_double_count(worker, model, tmp_path):
    """Regression: an imported STEP brings a container plus its children.

    Counting the App::Part container *and* its features doubled every volume.
    """
    target = tmp_path / "roundtrip.step"
    worker.call("export", {"document": "Block", "path": str(target), "format": "step"})
    opened = worker.call("open", {"path": str(target), "name": "Roundtrip"})
    measured = worker.call("measure", {"document": opened["name"]})
    expected = worker.call("measure", {"document": "Block"})["total"]["volume_mm3"]
    assert measured["total"]["volume_mm3"] == pytest.approx(expected, rel=1e-6)
    assert measured["bbox"]["size_mm"][2] == pytest.approx(35.0, rel=1e-6)
    worker.call("close", {"document": opened["name"]})


@pytest.mark.parametrize("failure", ["timeout", "crash"])
def test_real_worker_failure_recovery(failure):
    """Exercise actual embedded Python hangs/exits, not the protocol stand-in."""
    instance = FreeCADWorker(WorkerConfig(timeout=20))
    try:
        instance.start()
        first_pid = instance.describe()["pid"]
        instance.call("execute", {"code": "doc = App.newDocument('Lost')"})
        code = "import time; time.sleep(30)" if failure == "timeout" else "import os; os._exit(17)"
        with pytest.raises(FreeCADError) as error:
            instance.call("execute", {"code": code}, timeout=0.5 if failure == "timeout" else 20)
        assert error.value.kind == ("timeout" if failure == "timeout" else "worker_unavailable")
        assert not instance.alive
        info = instance.call("info")
        assert instance.describe()["pid"] != first_pid
        assert info["documents"] == []
        assert instance.call("execute", {"code": "result = 6 * 7"})["result"] == 42
    finally:
        instance.close()


def test_real_read_only_mcp_workflow(worker, model):
    """Real geometry remains readable through the restricted MCP surface."""
    import asyncio
    import os
    import sys

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def scenario():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "freecad_mcp", "--read-only", "--allow-path", str(model.parent)],
            # MCP forwards only a minimal default environment, excluding FREECADCMD.
            env={key: os.environ[key] for key in ("FREECADCMD",) if key in os.environ},
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                opened = await session.call_tool("open_document", {"path": str(model)})
                assert not opened.isError
                for tool in ("inspect_document", "measure", "bounding_box", "parameters"):
                    result = await session.call_tool(tool, {})
                    assert not result.isError
                measured = await session.call_tool("measure", {})
                import json
                data = json.loads(measured.content[0].text)
                assert data["total"]["volume_mm3"] == pytest.approx(6000 + math.pi * 9 * 25)
    asyncio.run(scenario())


def test_datum_geometry_is_ignored(worker, tmp_path):
    """Regression: PartDesign datum planes carry infinite faces.

    They are construction geometry, not material. Including them produced a volume of
    ~1e168 mm3 and a 2e100 mm bounding box on a real assembly.
    """
    path = tmp_path / "datum.FCStd"
    code = f"""
import FreeCAD, Part
doc = FreeCAD.newDocument("Datum")
box = doc.addObject("Part::Box", "Box")
box.Length, box.Width, box.Height = 10.0, 20.0, 30.0
plane = doc.addObject("PartDesign::Plane", "DatumPlane")
plane.MapMode = "Deactivated"
doc.recompute()
doc.saveAs({str(path)!r})
result = doc.Name
"""
    worker.call("execute", {"code": code})
    measured = worker.call("measure", {"document": "Datum"})
    assert measured["total"]["volume_mm3"] == pytest.approx(6000.0, rel=1e-6)
    assert measured["bbox"]["size_mm"] == pytest.approx([10.0, 20.0, 30.0], rel=1e-6)
    assert [o["name"] for o in measured["objects"]] == ["Box"]
    worker.call("close", {"document": "Datum"})
