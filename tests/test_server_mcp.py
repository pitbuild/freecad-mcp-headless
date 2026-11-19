"""End-to-end MCP test: real stdio transport, fake FreeCAD worker."""

import asyncio
import sys

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


def _run(server_args):
    async def scenario():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "freecad_mcp", *server_args],
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                tools = await session.list_tools()
                names = {tool.name for tool in tools.tools}
                assert "freecad_info" in names
                assert "measure" in names
                assert "screenshot" in names

                info = await session.call_tool("freecad_info", {})
                assert not info.isError
                assert '"info"' in info.content[0].text

                rejected = await session.call_tool("open_document", {"path": "/etc/passwd"})
                assert rejected.isError
                assert "allowed" in rejected.content[0].text.lower()

                return names

    return asyncio.run(scenario())


def test_stdio_handshake_and_guard(fake_freecad, allowed_dir):
    names = _run(["--allow-path", str(allowed_dir), "--freecadcmd", fake_freecad])
    assert {"open_document", "inspect_document", "export_document", "execute_python"} <= names


def test_no_execute_removes_tool(fake_freecad, allowed_dir):
    names = _run(
        ["--allow-path", str(allowed_dir), "--freecadcmd", fake_freecad, "--no-execute"]
    )
    assert "execute_python" not in names


def test_read_only_stdio_workflow(fake_freecad, allowed_dir, sample_step):
    async def scenario():
        params = StdioServerParameters(
            command=sys.executable,
            args=["-m", "freecad_mcp", "--allow-path", str(allowed_dir),
                  "--freecadcmd", fake_freecad, "--read-only"],
        )
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                names = {t.name for t in (await session.list_tools()).tools}
                disabled = {
                    "execute_python", "save_document", "export_document", "set_parameter",
                    "recompute", "screenshot", "restart_worker",
                }
                assert not names & disabled
                assert names == {
                    "freecad_info", "open_document", "close_document", "list_documents",
                    "inspect_document", "measure", "bounding_box", "parameters", "worker_status",
                }
                for tool, args in [
                    ("open_document", {"path": str(sample_step)}),
                    ("inspect_document", {}), ("measure", {}), ("parameters", {}),
                    ("close_document", {}),
                ]:
                    result = await session.call_tool(tool, args)
                    assert not result.isError
                for tool in disabled:
                    assert (await session.call_tool(tool, {})).isError
    asyncio.run(scenario())
