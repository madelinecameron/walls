import pytest
from mcp.server.mcpserver.exceptions import ToolError

from walls.server import INSTRUCTIONS, build_server


@pytest.fixture
def server(db, ai, settings):
    return build_server(db, ai, settings)


async def call(server, tool, **args):
    result = await server.call_tool(tool, args)
    return result.structuredContent or result.structured_content


async def test_tools_are_the_ones_the_instructions_name(server):
    names = {tool.name for tool in await server.list_tools()}
    assert names == {
        "wall_report",
        "walls_top",
        "wall_resolve",
        "walls_review",
        "wall_merge",
        "wall_keep_separate",
    }
    for name in names:
        assert name in INSTRUCTIONS or name in {"walls_top", "walls_review"}


async def test_report_then_top_then_resolve(server):
    report = await server.call_tool(
        "wall_report",
        {
            "action_needed": "Rotate the Vault token",
            "system": "Vault",
            "blocker_type": "credential",
            "why_blocked": "Token is dead",
            "repo": "walls",
        },
    )
    assert "times_seen" in str(report)

    top = await server.call_tool("walls_top", {})
    assert "Rotate the Vault token" in str(top) and "vault" in str(top)

    await server.call_tool("wall_resolve", {"wall_id": 1, "resolution": "Added a renewal timer"})
    assert "Rotate" not in str(await server.call_tool("walls_top", {}))
    assert "Rotate" in str(await server.call_tool("walls_top", {"status": "resolved"}))


async def test_bad_blocker_type_and_unknown_wall_are_refused(server):
    with pytest.raises(ToolError):
        await server.call_tool(
            "wall_report",
            {
                "action_needed": "Rotate the token",
                "system": "vault",
                "blocker_type": "annoying",
                "why_blocked": "Token is dead",
            },
        )
    with pytest.raises(ToolError, match="no wall #99"):
        await server.call_tool("wall_resolve", {"wall_id": 99, "resolution": "Fixed it"})
