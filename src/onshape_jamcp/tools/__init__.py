"""MCP tool definitions, grouped by theme. Each module exposes ``register(mcp, app, tool)``."""

from mcp.server.mcpserver import MCPServer

from ..app import App
from . import documents, inspection, modeling, output, raw, system
from .common import make_registrar


def register_all(mcp: MCPServer, app: App) -> None:
    tool = make_registrar(mcp, app)
    for module in (system, documents, inspection, modeling, output, raw):
        module.register(mcp, app, tool)
