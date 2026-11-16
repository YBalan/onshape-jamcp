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
5. Custom features (Feature Studio scripts such as Thread Creator): list_custom_features \
-> add_custom_feature. ThreadCreator takes the selected face as the thread's MAJOR diameter \
(a nut's hole must be Ø10 for M10, not the tap-drill size) and defaults to internal ANSI \
threads: pass internalThreads, threadProfile (ISO_STD) and the pitch p explicitly. Check the \
result by volume, not only by status. Details: docs/threads.md in the repository.
6. Repeat and shape: circular_pattern / linear_pattern / mirror (pass feature_ids - the \
feature that makes ONE spoke or hole - so the copies merge or cut like it, or part_ids for \
separate copies), revolve (profile plus a construction-line axis), shell, boolean. Their \
replies carry `effect` (parts and volume before/after) and a warning when nothing changed: \
Onshape can accept a feature that does nothing, so read it. Put counts in a variable \
('#spokes') when they should be configurable.
7. Anything else: onshape_api_request / add_feature / run_featurescript.
Defaults for every model, unless the user says otherwise: \
(a) NAME things - give each part a descriptive name (extrude part_name / rename) and rename a \
tab that still says "Part Studio N" or "Assembly N"; keep names the user chose. Extrudes are \
named with their size by default ("Extrude 8 mm", "Cut 3 mm", "Extrude (head_h)"); pass \
hole=true for a drilled hole (named "Hole", no size). Never put "#word" in a name you choose: \
Onshape reads it as a parameter of that feature and shows "?" - write variables as (word). \
(b) Put key dimensions (diameter, length, pitch, wall, ...) in VARIABLES with set_variable and \
use "#name" for sizes in sketches (circle, rectangle width/height, hexagon), extrude depth, \
fillets and thread pitch; use plain numbers only for incidental values. \
Only when the user asks: ASSEMBLIES (insert_instance with the first part fixed, then \
add_mate: check the positions it returns - an accepted mate is not proof it moved \
anything) and CONFIGURATIONS (set_configuration_input, set_variable by_option, \
then pass configuration to get_part_properties / capture_screenshot). \
Fewer requests: set many variables with set_variable(variables={...}) and change several \
features with update_feature(updates=[...]) - each is one Onshape request however many. \
Onshape limits each endpoint per day: avoid needless list_features calls and read a 429 \
message that says "daily limit" as "stop retrying".
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
