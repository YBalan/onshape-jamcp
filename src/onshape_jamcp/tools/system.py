"""Server/diagnostic tools."""

from typing import Any

from mcp.server.mcpserver import MCPServer

from ..app import App
from ..errors import OnshapeError
from .common import Registrar


def register(mcp: MCPServer, app: App, tool: Registrar) -> None:
    @tool(title="Get server settings", read_only=True)
    async def get_settings() -> dict[str, Any]:
        """Show this server's active settings: the unit system every length uses (metric = mm by
        default, or imperial = inches), whether screenshots are sent to you for visual review,
        read-only mode, and the Onshape API target. Never reveals API keys. Call this first so
        you know which unit to use for lengths."""
        return app.settings.public_dict()

    @tool(title="Check Onshape connection", read_only=True)
    async def whoami() -> dict[str, Any]:
        """Verify that the configured credentials (API keys or OAuth sign-in) work by asking
        Onshape which account they belong to. Use it to diagnose authentication problems before
        doing real work."""
        info = await app.client.get("/users/sessioninfo")
        if not info.get("id"):
            # Onshape answers 200 with an empty body to requests it could not authenticate, so
            # a successful HTTP status alone does not prove the credentials work.
            raise OnshapeError(
                401,
                "Onshape answered but did not recognise the credentials (anonymous session)",
                hint=app.settings.auth_help,
            )
        return {
            "connected": True,
            "user_id": info.get("id"),
            "name": info.get("name"),
            "base_url": app.settings.base_url,
        }
