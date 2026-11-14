"""Generic escape hatch for the whole Onshape REST API."""

import json
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from ..app import App
from ..errors import ConfigError, InputError
from .common import Registrar

MAX_RESPONSE_CHARS = 40_000


def validate_api_path(path: str) -> str:
    """Accept only plain relative API paths: no scheme/host, no traversal, no control chars."""
    cleaned = path.strip()
    if (
        not cleaned.startswith("/")
        or cleaned.startswith("//")
        or "://" in cleaned
        or "\\" in cleaned
        or ".." in cleaned
        or "?" in cleaned
        or "#" in cleaned
        or any(ord(c) < 32 for c in cleaned)
    ):
        raise InputError(
            "path must be a relative Onshape API path like '/documents' or "
            "'/partstudios/d/<did>/w/<wid>/e/<eid>/featurespecs' (put query parameters in "
            "`query`, not in the path)."
        )
    return cleaned


def register(mcp: MCPServer, app: App, tool: Registrar) -> None:
    @tool(title="Call any Onshape API endpoint", destructive=True)
    async def onshape_api_request(
        method: Annotated[Literal["GET", "POST", "DELETE"], Field(description="HTTP method.")],
        path: Annotated[
            str,
            Field(
                description="API path relative to /api/vNN, e.g. '/documents' or "
                "'/partstudios/d/<did>/w/<wid>/e/<eid>/featurespecs'. See the Onshape API "
                "Explorer: https://cad.onshape.com/glassworks/explorer"
            ),
        ],
        query: Annotated[
            dict[str, str | int | float | bool | list[str]] | None,
            Field(description="Query-string parameters."),
        ] = None,
        body: Annotated[
            dict[str, Any] | None, Field(description="JSON request body for POST.")
        ] = None,
    ) -> dict[str, Any]:
        """Escape hatch: call any Onshape REST endpoint directly. Use it for things the
        dedicated tools do not cover. WARNING: this is the raw API - lengths are in METERS (no
        unit conversion), POST/DELETE can change or destroy data, and in read-only mode only GET
        is allowed. Large responses are truncated."""
        clean_path = validate_api_path(path)
        if method != "GET" and app.settings.read_only:
            raise ConfigError(
                f"{method} is disabled: the server is in read-only mode (ONSHAPE_READ_ONLY=true)."
            )
        data = await app.client.request(method, clean_path, params=query, body=body)
        text = json.dumps(data, separators=(",", ":"))
        if len(text) > MAX_RESPONSE_CHARS:
            return {
                "truncated": True,
                "total_chars": len(text),
                "preview": text[:MAX_RESPONSE_CHARS],
            }
        return {"response": data}
