"""Server assembly: wires settings, the Onshape client and the tools into an MCP server."""

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Literal

import httpx
from mcp.server.mcpserver import MCPServer

from . import __version__
from .app import App
from .config import Settings
from .tools import register_all

LogLevel = Literal["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"]

INSTRUCTIONS = """\
Onshape CAD server. Typical workflow:
1. get_settings - learn the length unit (metric mm by default, or imperial inches). \
Every length you send or receive uses that unit; angles are degrees.
2. list_documents / get_document - find document_id and element_id (a Part Studio tab). \
create_document + create_element start from scratch.
3. Model: create_sketch (shapes on Top/Front/Right or a face) -> extrude (operation \
new/add/remove) -> fillet / chamfer. Use list_features to check every feature reports OK.
4. Verify: get_part_properties (dimensions), get_body_details (face/edge IDs), \
capture_screenshot (visual check; images cost tokens only when screenshot review is on).
5. Anything else: onshape_api_request / add_feature / run_featurescript.
Never guess IDs - read them from tool results."""


def build_server(
    settings: Settings,
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    log_level: LogLevel = "WARNING",
) -> MCPServer:
    """Create the MCP server. ``transport`` lets tests substitute a fake Onshape."""
    app = App(settings, transport=transport)
    if log_level != "DEBUG":  # httpx logs every request at INFO; keep stderr quiet
        logging.getLogger("httpx").setLevel(logging.WARNING)

    @asynccontextmanager
    async def lifespan(_server: MCPServer) -> AsyncIterator[App]:
        try:
            yield app
        finally:
            await app.aclose()

    mcp = MCPServer(
        name="onshape-jamcp",
        title="Onshape (JAMCP)",
        instructions=INSTRUCTIONS,
        version=__version__,
        lifespan=lifespan,
        log_level=log_level,
    )
    register_all(mcp, app)
    return mcp
