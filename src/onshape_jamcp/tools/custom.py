"""Custom features: Feature Studio scripts (Thread Creator, ...) used inside Part Studios."""

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from .. import custom_features as cf
from .. import features
from ..app import App
from ..client import q
from ..errors import InputError
from .common import DocumentId, ElementId, Registrar, WorkspaceId

_NOT_LISTED = (
    "No custom features were reported. Onshape lists a custom feature only when it is available "
    "to this Part Studio; the Feature Scripts on your account toolbar are an account setting "
    "that the API does not list. Either (1) add the feature once from the Custom features "
    "toolbar of this Part Studio in Onshape and call this tool again, or (2) pass "
    "feature_studio_url: the browser URL of the Feature Studio tab that defines it."
)
_NEEDS_VERSION = (
    "That Feature Studio is in another document, and Onshape links custom features to a "
    "*version* of the document that defines them. Open that document, make or pick a version "
    "(Versions and history) and pass the URL of the Feature Studio tab at that version "
    "(.../documents/<did>/v/<version>/e/<eid>)."
)


_SPECS_TTL = 300.0  # seconds: the specs are ~9 MB and rarely change within a session


def _from_other_document(studio: tuple[str, str, str, str] | None, document_id: str) -> bool:
    return studio is not None and studio[0] != document_id


def register(mcp: MCPServer, app: App, tool: Registrar) -> None:
    async def load_specs(
        document_id: str,
        workspace_id: str,
        element_id: str,
        studio: tuple[str, str, str, str] | None,
        fresh: bool = False,
    ) -> tuple[list[dict[str, Any]], str]:
        """Feature specs from the Feature Studio ``studio`` (parsed), else from the Part Studio."""
        if studio:
            did, wvm, wvmid, eid = studio
            path = f"/featurestudios/d/{q(did)}/{wvm}/{q(wvmid)}/e/{q(eid)}/featurespecs"
            source = "feature_studio"
        else:
            path = f"/partstudios/d/{q(document_id)}/w/{q(workspace_id)}/e/{q(element_id)}"
            path += "/featurespecs"
            source = "part_studio"
        data = await app.cached(path, _SPECS_TTL, lambda: app.client.get(path), fresh=fresh)
        specs = [s for s in data.get("featureSpecs") or [] if isinstance(s, dict)]
        if studio and _from_other_document(studio, document_id) and studio[1] == "v":
            specs = [cf.qualify_namespace(s, studio) for s in specs]  # d..::v..::e..::m..
        return specs, source

    @tool(title="List custom features", read_only=True)
    async def list_custom_features(
        document_id: DocumentId,
        element_id: ElementId,
        workspace_id: WorkspaceId = None,
        feature_studio_url: Annotated[
            str | None,
            Field(
                description="Optional browser URL of a Feature Studio tab (.../documents/<did>/"
                "w|v|m/<id>/e/<eid>). Lists the custom features that studio defines instead of "
                "the ones this Part Studio already knows about. Use it for a Feature Script "
                "added to your account that is not yet in this Part Studio. To add features "
                "from another document, use its version URL (/v/)."
            ),
        ] = None,
    ) -> dict[str, Any]:
        """List custom features (Feature Studio scripts such as Thread Creator) that
        add_custom_feature can add: feature_type, namespace and every parameter with its type,
        unit, default and options. Also lists the custom features this Part Studio already
        uses. Built-in Onshape features are not listed - use the dedicated tools for those."""
        studio = cf.parse_feature_studio_url(feature_studio_url) if feature_studio_url else None
        wid = await app.workspace(document_id, workspace_id)
        specs, source = await load_specs(document_id, wid, element_id, studio, fresh=True)
        listed = await app.client.get(
            f"/partstudios/d/{q(document_id)}/w/{q(wid)}/e/{q(element_id)}/features"
        )
        states = listed.get("featureStates") or {}
        in_use = [
            {
                "feature_id": f.get("featureId"),
                "name": f.get("name"),
                "feature_type": f.get("featureType"),
                "namespace": f.get("namespace"),
                "status": (states.get(f.get("featureId")) or {}).get("featureStatus"),
            }
            for f in listed.get("features") or []
            if cf.is_custom(f)
        ]
        result: dict[str, Any] = {
            "source": source,
            "custom_features": [cf.summarize_spec(s, app.units) for s in specs if cf.is_custom(s)],
            "used_in_this_part_studio": in_use,
        }
        if not result["custom_features"]:
            result["hint"] = _NOT_LISTED
        elif _from_other_document(studio, document_id) and studio and studio[1] != "v":
            result["note"] = _NEEDS_VERSION
        return result

    @tool(title="Add custom feature", writes=True)
    async def add_custom_feature(
        document_id: DocumentId,
        element_id: ElementId,
        feature_type: Annotated[
            str,
            Field(
                min_length=1,
                description="feature_type of the custom feature, from list_custom_features.",
            ),
        ],
        parameters: Annotated[
            dict[str, float | int | str | bool | list[str]] | None,
            Field(
                description="Map of parameter ID to value, using the IDs from "
                "list_custom_features. Lengths are numbers in the configured unit (strings "
                "may carry units or be expressions such as '#width'); angles are degrees; "
                "enums take one of the listed options; booleans are true/false; selections "
                "(type 'query') are lists of face / edge / part IDs from get_body_details or "
                "list_parts. Anything left out keeps the script's own default."
            ),
        ] = None,
        name: Annotated[
            str | None,
            Field(min_length=1, max_length=100, description="Name; defaults to the script's."),
        ] = None,
        namespace: Annotated[
            str | None,
            Field(
                description="Only to choose between custom features that share a feature_type; "
                "copy it exactly from list_custom_features."
            ),
        ] = None,
        feature_studio_url: Annotated[
            str | None,
            Field(
                description="Browser URL of the Feature Studio tab that defines the feature, for "
                "a Feature Script that list_custom_features does not show for this Part Studio. "
                "From another document it must be a version URL (/documents/<did>/v/<vid>/e/<eid>)."
            ),
        ] = None,
        workspace_id: WorkspaceId = None,
    ) -> dict[str, Any]:
        """Add a custom feature (a Feature Studio script such as Thread Creator) to a Part Studio.
        Find its feature_type and parameter IDs with list_custom_features first. Returns the new
        feature_id and its health status; if that is ERROR or WARNING, inspect it with
        list_features(feature_id=...), then fix it with update_feature or remove it with
        delete_feature."""
        studio = cf.parse_feature_studio_url(feature_studio_url) if feature_studio_url else None
        if studio and _from_other_document(studio, document_id) and studio[1] != "v":
            raise InputError(_NEEDS_VERSION)
        wid = await app.workspace(document_id, workspace_id)
        specs, _ = await load_specs(document_id, wid, element_id, studio)
        try:
            spec = cf.pick_spec(specs, feature_type, namespace)
        except InputError:  # maybe the script was added or changed since the cached copy
            specs, _ = await load_specs(document_id, wid, element_id, studio, fresh=True)
            spec = cf.pick_spec(specs, feature_type, namespace)
        body = cf.build_custom_feature(spec, name=name, values=parameters or {}, units=app.units)
        path = f"/partstudios/d/{q(document_id)}/w/{q(wid)}/e/{q(element_id)}/features"
        response = await app.client.post(path, body)
        # The reply to the POST is not regenerated yet: live, a custom feature that then failed
        # was still reported as OK. Read the real state back.
        feature_id = (response.get("feature") or {}).get("featureId")
        state = ((await app.client.get(path)).get("featureStates") or {}).get(feature_id)
        if state:
            response = {**response, "featureState": state}
        result = features.summarize_feature_response(response)
        result["namespace"] = body["feature"]["namespace"]
        if result["status"] == "ERROR":
            result["hint"] = cf.ERROR_HINT
        return result
