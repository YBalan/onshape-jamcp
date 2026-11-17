"""Launch the real server process over stdio, as an MCP client (Claude Desktop, Glama) would."""

import os
import subprocess
import sys

from mcp import Client, StdioServerParameters
from mcp_types import TextContent

from onshape_jamcp import __version__

CLEAN_ENV = {k: v for k, v in os.environ.items() if not k.startswith("ONSHAPE_")}


def params(*args: str, **env: str) -> StdioServerParameters:
    return StdioServerParameters(
        command=sys.executable,
        args=["-I", "-m", "onshape_jamcp", *args],
        env={**CLEAN_ENV, **env},
    )


async def test_process_starts_without_credentials_and_lists_tools():
    async with Client(params()) as client:
        tools = (await client.list_tools()).tools
        assert {"create_sketch", "capture_screenshot", "get_settings"} <= {t.name for t in tools}
        settings = await client.call_tool("get_settings", {})
        assert settings.structured_content["units"] == "metric"
        assert settings.structured_content["credentials_configured"] is False


async def test_flags_override_environment():
    env = {"ONSHAPE_UNITS": "metric", "ONSHAPE_SCREENSHOT_REVIEW": "true"}
    async with Client(params("--units", "imperial", "--no-screenshot-review", **env)) as client:
        data = (await client.call_tool("get_settings", {})).structured_content
    assert data["units"] == "imperial"
    assert data["screenshot_review"] is False


async def test_read_only_flag_removes_write_tools():
    async with Client(params("--read-only")) as client:
        names = {t.name for t in (await client.list_tools()).tools}
    assert "extrude" not in names and "list_features" in names


async def test_tool_call_without_credentials_explains_what_to_do():
    async with Client(params()) as client:
        result = await client.call_tool("list_documents", {})
    assert result.is_error
    text = " ".join(c.text for c in result.content if isinstance(c, TextContent))
    assert "ONSHAPE_ACCESS_KEY" in text and "cad.onshape.com/user/developer" in text


def test_invalid_configuration_exits_with_a_clear_message():
    done = subprocess.run(
        [sys.executable, "-I", "-m", "onshape_jamcp"],
        env={**CLEAN_ENV, "ONSHAPE_UNITS": "cubits"},
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert done.returncode == 2
    assert "ONSHAPE_UNITS" in done.stderr
    assert done.stdout == ""  # stdout is reserved for the protocol


def test_help_and_version():
    out = subprocess.run(
        [sys.executable, "-I", "-m", "onshape_jamcp", "--version"],
        capture_output=True,
        text=True,
        timeout=60,
        env=CLEAN_ENV,
    )
    # Never hard-code the version here: every release bumps it (that is how this once broke one).
    assert out.returncode == 0 and out.stdout.strip() == f"onshape-jamcp {__version__}"
    helped = subprocess.run(
        [sys.executable, "-I", "-m", "onshape_jamcp", "--help"],
        capture_output=True,
        text=True,
        timeout=60,
        env=CLEAN_ENV,
    )
    assert "ONSHAPE_UNITS" in helped.stdout
