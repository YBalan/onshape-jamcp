"""Document and element (tab) management."""

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from .. import names
from ..app import App
from ..client import q
from ..errors import InputError, OnshapeError
from .common import DocumentId, Registrar, WorkspaceId

_SCOPE_FILTER = {"mine": 0, "created": 1, "shared": 2, "public": 4, "recent": 5}
_SORT_COLUMN = {"modified": "modifiedAt", "created": "createdAt", "name": "name"}


def _element_entry(e: dict[str, Any]) -> dict[str, Any]:
    entry: dict[str, Any] = {
        "element_id": e.get("id"),
        "name": e.get("name"),
        "type": e.get("elementType"),
    }
    if names.is_default_tab_name(e.get("name")):
        entry["has_default_name"] = True  # rename it to say what it holds
    return entry


def register(mcp: MCPServer, app: App, tool: Registrar) -> None:
    @tool(title="List documents", read_only=True)
    async def list_documents(
        query: Annotated[
            str | None, Field(description="Case-insensitive text to look for in document names.")
        ] = None,
        scope: Annotated[
            Literal["mine", "created", "shared", "recent", "public"],
            Field(description="Which documents to list."),
        ] = "mine",
        sort_by: Annotated[Literal["modified", "created", "name"], Field()] = "modified",
        limit: Annotated[int, Field(ge=1, le=100, description="Page size.")] = 20,
        offset: Annotated[int, Field(ge=0, description="Skip this many results (paging).")] = 0,
    ) -> dict[str, Any]:
        """Find Onshape documents: returns each one's document_id (every other tool needs it),
        name, modified_at and owner, newest first unless `sort_by` says otherwise. Start here when
        the user names a document: search with `query`, narrow with `scope` (your own, created by
        you, shared with you, recent, or public). To open one document and see its tabs use
        `get_document`; to make a new one `create_document`. Read-only. A full page includes a
        `next_offset`: pass it as `offset` for the next page."""
        data = await app.client.get(
            "/documents",
            {
                "q": query,
                "filter": _SCOPE_FILTER[scope],
                "sortColumn": _SORT_COLUMN[sort_by],
                "sortOrder": "asc" if sort_by == "name" else "desc",
                "limit": limit,
                "offset": offset,
            },
        )
        items = data.get("items") or []
        return {
            "documents": [
                {
                    "document_id": d.get("id"),
                    "name": d.get("name"),
                    "modified_at": d.get("modifiedAt"),
                    "owner": (d.get("owner") or {}).get("name"),
                }
                for d in items
            ],
            "next_offset": offset + len(items) if data.get("next") else None,
        }

    @tool(title="Get document", read_only=True)
    async def get_document(
        document_id: DocumentId, workspace_id: WorkspaceId = None
    ) -> dict[str, Any]:
        """Describe one document: its main workspace ID and every element (tab) in it - Part
        Studios, Assemblies, Drawings, imported files - with their element IDs and types. A tab
        flagged has_default_name still carries Onshape's placeholder name: rename it with `rename`
        to say what it holds."""
        doc = await app.client.get(f"/documents/{q(document_id)}")
        wid = workspace_id or (doc.get("defaultWorkspace") or {}).get("id")
        if not wid:
            raise InputError("The document has no default workspace; pass workspace_id.")
        elements = await app.client.get(f"/documents/d/{q(document_id)}/w/{q(wid)}/elements")
        return {
            "document_id": doc.get("id", document_id),
            "name": doc.get("name"),
            "description": doc.get("description") or None,
            "workspace_id": wid,
            "modified_at": doc.get("modifiedAt"),
            "url": f"{app.settings.base_url}/documents/{document_id}/w/{wid}",
            "elements": [
                _element_entry(e) for e in (elements if isinstance(elements, list) else [])
            ],
        }

    @tool(title="Create document", writes=True)
    async def create_document(
        name: Annotated[str, Field(min_length=1, max_length=200, description="Document name.")],
        description: Annotated[str | None, Field(description="Optional description.")] = None,
        public: Annotated[
            bool,
            Field(
                description="Make the document visible to everyone. Leave false unless the "
                "user's plan requires it: Onshape's FREE plan can only create public documents "
                "(the call then fails and tells you to retry with public=true - only do that "
                "after the user agrees)."
            ),
        ] = False,
    ) -> dict[str, Any]:
        """Create a new, empty-looking Onshape document in the user's account and return its
        document_id, workspace_id, name, url and the tabs it already has (Onshape usually adds a
        'Part Studio 1' and an 'Assembly 1': reuse and `rename` them before adding more with
        `create_element`). Private by default. Onshape's FREE plan only allows PUBLIC documents:
        the call then fails with nothing created and a message to ask the user, because a public
        document is visible to everyone; retry with public=true only after they agree. Use it only
        when the user wants a new document; to work in an existing one find it with
        `list_documents`. There is no tool to delete a document, so tell the user a scratch
        document stays in their account until they delete it in Onshape."""
        try:
            doc = await app.client.post(
                "/documents", {"name": name, "description": description, "isPublic": public}
            )
        except OnshapeError as exc:
            if exc.status == 409 and "public" in exc.message.lower() and not public:
                raise InputError(
                    "This Onshape plan only allows PUBLIC documents (free plan). Nothing was "
                    "created. Ask the user whether they accept a public document, then call "
                    "create_document again with public=true. "
                    f"Onshape said: {exc.message}"
                ) from None
            raise
        did = doc.get("id")
        wid = (doc.get("defaultWorkspace") or {}).get("id")
        elements: list[dict[str, Any]] = []
        if did and wid:
            listed = await app.client.get(f"/documents/d/{q(did)}/w/{q(wid)}/elements")
            elements = [_element_entry(e) for e in (listed if isinstance(listed, list) else [])]
        return {
            "document_id": did,
            "workspace_id": wid,
            "name": doc.get("name", name),
            "url": f"{app.settings.base_url}/documents/{did}/w/{wid}",
            "elements": elements,
        }

    @tool(title="Create Part Studio or Assembly", writes=True)
    async def create_element(
        document_id: DocumentId,
        name: Annotated[str, Field(min_length=1, max_length=200, description="Tab name.")],
        kind: Annotated[
            Literal["part_studio", "assembly"], Field(description="Type of element to add.")
        ] = "part_studio",
        workspace_id: WorkspaceId = None,
    ) -> dict[str, Any]:
        """Add a new tab to a document and return its element_id, name and kind: a Part Studio
        (where sketches, extrudes and the other features build parts) or an Assembly (where parts
        are inserted and mated). Use it when the document has no suitable tab - a new document
        already has a Part Studio and an Assembly, so check `get_document` first and reuse them
        instead of piling up empty tabs. Give it a descriptive name (a tab still called 'Part
        Studio 1' is flagged has_default_name). The new tab is empty: model in it with
        `create_sketch` / `extrude`, or fill an Assembly with `insert_instance`. It does not
        create parts, sketches or documents (`create_document` makes those)."""
        wid = await app.workspace(document_id, workspace_id)
        collection = "partstudios" if kind == "part_studio" else "assemblies"
        element = await app.client.post(
            f"/{collection}/d/{q(document_id)}/w/{q(wid)}", {"name": name}
        )
        element_id = element.get("id") or element.get("elementId")
        if not element_id:
            raise InputError(f"Onshape did not return an element ID: {element!r}")
        return {"element_id": element_id, "name": element.get("name", name), "kind": kind}
