"""Putting parts into an Assembly and mating them.

Mates are built the way Onshape builds them (see ``mates.py``): learned from a mate made by hand
and verified by solving it (the second part moves). Ground the first instance with
``insert_instance(fixed=True)`` so the second one is the part that moves.
"""

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from .. import configurations, features, mates
from ..app import App
from ..client import q
from ..errors import InputError, OnshapeError
from .common import Configuration, DocumentId, ElementId, Registrar, WorkspaceId


class MateSide(BaseModel):
    """One of the two things a mate joins: a spot on a part instance."""

    instance_id: str = Field(
        min_length=1, description="Instance ID from insert_instance or get_assembly."
    )
    entity_id: str | None = Field(
        default=None,
        description="Face / edge / vertex ID on that instance's part (get_body_details of its "
        "Part Studio). Not needed for at='origin'.",
    )
    at: Literal[
        "center", "centroid", "point", "mid_point", "origin", "top_axis", "mid_axis", "bottom_axis"
    ] = Field(
        default="center",
        description="Where the mate connector sits: 'center' of a circular edge, 'centroid' of a "
        "face, a 'point' (vertex), the 'mid_point' of an edge, the part's 'origin', or a "
        "cylinder's 'top_axis' / 'mid_axis' / 'bottom_axis' point. center and centroid are "
        "verified live; the others use the same mechanism.",
    )


def register(mcp: MCPServer, app: App, tool: Registrar) -> None:
    async def root_assembly(document_id: str, workspace_id: str, assembly_id: str) -> dict:
        data = await app.client.get(
            f"/assemblies/d/{q(document_id)}/w/{q(workspace_id)}/e/{q(assembly_id)}"
        )
        return data.get("rootAssembly") or {}

    @tool(title="Insert a part into an assembly", writes=True)
    async def insert_instance(
        document_id: DocumentId,
        assembly_id: Annotated[
            str, Field(min_length=1, description="Element ID of the Assembly tab (get_document).")
        ],
        source_element_id: ElementId,
        part_id: Annotated[
            str | None,
            Field(
                description="Part ID (list_parts) of the one part to insert. Leave out to insert "
                "the whole Part Studio as one instance."
            ),
        ] = None,
        source_document_id: Annotated[
            str | None,
            Field(description="Document that holds the Part Studio; defaults to this document."),
        ] = None,
        configuration: Annotated[
            Configuration | None,
            Field(
                description="Variant of a configured Part Studio to insert, e.g. {'Size': 'M18'}."
            ),
        ] = None,
        fixed: Annotated[
            bool,
            Field(description="Ground the instance (it cannot move). Usually the first one."),
        ] = False,
        workspace_id: WorkspaceId = None,
    ) -> dict[str, Any]:
        """Insert a part (or a whole Part Studio) into an Assembly and return the new instances'
        IDs and names. Name the Assembly tab (rename) if it still says 'Assembly N'. Only when
        the user asks for an assembly. Join parts with add_mate."""
        configurations.encode(configuration, app.units)  # validate before any request
        wid = await app.workspace(document_id, workspace_id)
        cfg = None
        if configuration:
            source_document = source_document_id or document_id
            try:
                source_wid = (
                    wid if source_document == document_id else await app.workspace(source_document)
                )
                cfg = await app.configuration_param(
                    source_document, source_wid, source_element_id, configuration
                )
            except OnshapeError:  # cannot look the inputs up: send the names as given
                cfg = configurations.encode(configuration, app.units)
        root = await root_assembly(document_id, wid, assembly_id)
        before = {i.get("id") for i in root.get("instances") or []}
        body: dict[str, Any] = {
            "documentId": source_document_id or document_id,
            "elementId": source_element_id,
            "isAssembly": False,
            "isWholePartStudio": part_id is None,
            "isFixed": fixed,
        }
        if part_id:
            body["partId"] = part_id
        if cfg:
            body["configuration"] = cfg
        await app.client.post(
            f"/assemblies/d/{q(document_id)}/w/{q(wid)}/e/{q(assembly_id)}/instances", body
        )
        after = (await root_assembly(document_id, wid, assembly_id)).get("instances") or []
        created = [
            {"instance_id": i.get("id"), "name": i.get("name")}
            for i in after
            if i.get("id") not in before
        ]
        return {"instances": created, "fixed": fixed, "configuration": cfg}

    @tool(title="Add a mate", writes=True)
    async def add_mate(
        document_id: DocumentId,
        assembly_id: Annotated[
            str, Field(min_length=1, description="Element ID of the Assembly tab (get_document).")
        ],
        mate_type: Annotated[
            Literal[
                "fastened",
                "revolute",
                "slider",
                "planar",
                "cylindrical",
                "pin_slot",
                "ball",
                "parallel",
            ],
            Field(
                description="fastened: no motion; revolute: rotates about the axis; cylindrical: "
                "rotates and slides along it (a nut on a bolt); slider, planar, pin_slot, ball, "
                "parallel as in Onshape."
            ),
        ],
        first: MateSide,
        second: MateSide,
        name: Annotated[
            str | None,
            Field(min_length=1, max_length=100, description="Defaults to e.g. 'Cylindrical 1'."),
        ] = None,
        flip: Annotated[
            bool, Field(description="Flip the primary axis (the second part faces the other way).")
        ] = False,
        offset: Annotated[
            list[float | str] | None,
            Field(
                min_length=3,
                max_length=3,
                description="[x, y, z] offset between the connectors, in the configured length "
                "unit (strings may carry units).",
            ),
        ] = None,
        workspace_id: WorkspaceId = None,
    ) -> dict[str, Any]:
        """Join two part instances of an Assembly with a mate. Pick a spot on each: for a nut on a
        bolt use the bolt's circular end edge (at 'center') and a face of the nut (at 'centroid'),
        with mate_type 'cylindrical' (free to turn and slide) or 'fastened'. The first instance
        stays where it is if it is grounded; the second moves to satisfy the mate. Returns the
        real status and both instances' resulting positions. Only when the user asks for an
        assembly. Note: the centroid of a threaded face can sit ~0.2 mm off the thread's axis."""
        wid = await app.workspace(document_id, workspace_id)
        root = await root_assembly(document_id, wid, assembly_id)
        known = {i.get("id"): i.get("name") for i in root.get("instances") or []}
        for side in (first, second):
            if side.instance_id not in known:
                raise InputError(
                    f"No instance {side.instance_id!r} in this Assembly. Instances: {known}."
                )
        if first.instance_id == second.instance_id:
            raise InputError("A mate joins two different instances.")
        label = mate_type.replace("_", " ").capitalize()
        existing = sum(
            1
            for f in root.get("features") or []
            if str((f.get("featureData") or {}).get("name") or f.get("name") or "").startswith(
                label
            )
        )
        body = mates.mate_feature(
            name=name or f"{label} {existing + 1}",
            mate_type=mate_type,
            sides=[(s.instance_id, s.at, s.entity_id) for s in (first, second)],
            flip=flip,
            offset=offset,
            units=app.units,
        )
        base = f"/assemblies/d/{q(document_id)}/w/{q(wid)}/e/{q(assembly_id)}/features"
        result = features.summarize_feature_response(await app.client.post(base, body))
        # Accepting a mate says nothing about it working (seen live): read the state back and
        # report where the parts ended up.
        state = ((await app.client.get(base)).get("featureStates") or {}).get(result["feature_id"])
        if state:
            result["status"] = state.get("featureStatus", result["status"])
        placed = {
            tuple(o.get("path") or ()): o
            for o in (await root_assembly(document_id, wid, assembly_id)).get("occurrences") or []
        }

        def position(instance_id: str) -> list[float]:
            t = (placed.get((instance_id,)) or {}).get("transform") or []
            point = [t[3], t[7], t[11]] if len(t) >= 12 else [0.0, 0.0, 0.0]
            return app.units.point_from_meters(point)

        result["mate_type"] = mate_type
        result["positions"] = [
            {
                "instance_id": s.instance_id,
                "name": known[s.instance_id],
                "position": position(s.instance_id),
            }
            for s in (first, second)
        ]
        result["length_unit"] = app.units.length_unit
        if result["status"] != "OK":
            result["warning"] = (
                f"Onshape reports this mate as {result['status']}: check the entity IDs "
                "(a circular edge for 'center', a face for 'centroid') and that both belong to "
                "the named instances."
            )
        return result
