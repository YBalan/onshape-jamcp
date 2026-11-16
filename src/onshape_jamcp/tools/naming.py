"""Naming parts and tabs."""

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from ..app import App
from .common import DocumentId, ElementId, Registrar, WorkspaceId


def register(mcp: MCPServer, app: App, tool: Registrar) -> None:
    @tool(title="Rename a part or tab", writes=True, idempotent=True)
    async def rename(
        document_id: DocumentId,
        element_id: ElementId,
        name: Annotated[
            str,
            Field(
                min_length=1,
                max_length=200,
                description="The new name, e.g. 'M10x40 Hex Bolt' for a part or 'M10 Fasteners' "
                "for a tab.",
            ),
        ],
        part_id: Annotated[
            str | None,
            Field(
                description="Part ID from list_parts to rename that part. Leave out to rename "
                "the whole tab (the Part Studio / Assembly itself)."
            ),
        ] = None,
        workspace_id: WorkspaceId = None,
    ) -> dict[str, Any]:
        """Give a part, or a whole tab (Part Studio / Assembly), a descriptive name. Do this by
        default: a part still called 'Part 2' or a tab still called 'Part Studio 1' says nothing.
        Keep a name the user chose; rename only placeholders (flagged has_default_name by
        get_document and list_parts) unless the user asks otherwise."""
        wid = await app.workspace(document_id, workspace_id)
        await app.rename(document_id, wid, element_id, name, part_id)
        return {"renamed": "part" if part_id else "tab", "element_id": element_id, "name": name} | (
            {"part_id": part_id} if part_id else {}
        )
