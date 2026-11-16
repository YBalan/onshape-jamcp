"""Revolve, shell and boolean: shaping solids beyond extrude."""

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from .. import features, names
from ..app import App
from ..errors import InputError
from .applying import Verify, apply_feature
from .common import DocumentId, ElementId, Registrar, WorkspaceId
from .modeling import Length


def register(mcp: MCPServer, app: App, tool: Registrar) -> None:
    @tool(title="Revolve sketch", writes=True)
    async def revolve(
        document_id: DocumentId,
        element_id: ElementId,
        sketch_feature_id: Annotated[
            str, Field(min_length=1, description="feature_id returned by create_sketch.")
        ],
        axis: Annotated[
            str | None,
            Field(
                description="The axis to turn about: the ID of an edge or cylindrical face (from "
                "get_body_details) or 'sketch:<feature_id>'. Omit it to use the construction line "
                "drawn in the SAME sketch (add a `line` shape with construction=true next to "
                "the profile); it must be the only construction line there."
            ),
        ] = None,
        angle: Annotated[
            float | str,
            Field(description="Angle in degrees (default 360: a full revolution)."),
        ] = 360,
        operation: Annotated[
            features.Operation,
            Field(
                description="'new' creates a separate part; 'add' merges into existing parts; "
                "'remove' cuts material away (a groove, a bore); 'intersect' keeps the overlap."
            ),
        ] = "new",
        symmetric: Annotated[
            bool, Field(description="Partial revolve: half the angle to each side of the sketch.")
        ] = False,
        opposite_direction: Annotated[bool, Field(description="Turn the other way.")] = False,
        only_parts: Annotated[
            list[str] | None,
            Field(
                description="Part IDs (list_parts) that 'add', 'remove' and 'intersect' should "
                "affect instead of every part touched."
            ),
        ] = None,
        part_name: Annotated[
            str | None,
            Field(
                min_length=1,
                max_length=200,
                description="Name for the part an operation 'new' creates, e.g. 'Flanged bushing'. "
                "Give one unless the user chose a name.",
            ),
        ] = None,
        name: Annotated[
            str | None,
            Field(
                min_length=1,
                max_length=100,
                description="Feature name (default 'Revolve 360 deg').",
            ),
        ] = None,
        verify: Verify = True,
        workspace_id: WorkspaceId = None,
    ) -> dict[str, Any]:
        """Revolve the closed regions of a sketch around an axis into a solid of revolution
        (a bottle, a pulley, a washer) or cut a groove with it (operation 'remove'). Draw the
        profile on one side of the axis and, in the same sketch, the axis as a construction
        line. The reply says what changed (parts, volume) and lists the created parts."""
        wid = await app.workspace(document_id, workspace_id)
        axis_id = await app.reference_id(
            document_id, wid, element_id, axis or f"sketch:{sketch_feature_id}", what="axis"
        )
        if (
            operation == "intersect"
            and not only_parts
            and len(await app.parts(document_id, wid, element_id)) > 1
        ):
            raise InputError(
                "An unscoped 'intersect' deletes every part that does not overlap the new body: "
                "pass only_parts with the part IDs to intersect (from list_parts)."
            )
        body = features.revolve_feature(
            name=name or names.revolve_name(app.units.angle_expression(angle)),
            sketch_feature_id=sketch_feature_id,
            axis_id=axis_id,
            angle=angle,
            operation=operation,
            units=app.units,
            symmetric=symmetric,
            opposite_direction=opposite_direction,
            only_parts=only_parts,
        )
        return await apply_feature(
            app, document_id, element_id, wid, body, verify=verify, part_name=part_name
        )

    @tool(title="Shell", writes=True)
    async def shell(
        document_id: DocumentId,
        element_id: ElementId,
        thickness: Length,
        remove_face_ids: Annotated[
            list[str] | None,
            Field(
                description="Faces to open up (from get_body_details): the part becomes a hollow "
                "shell open at these faces, like a box without a lid. Give exactly one of "
                "remove_face_ids and hollow_part_ids."
            ),
        ] = None,
        hollow_part_ids: Annotated[
            list[str] | None,
            Field(
                description="Parts (list_parts) to hollow out completely, leaving a closed void "
                "inside. Give exactly one of remove_face_ids and hollow_part_ids."
            ),
        ] = None,
        opposite_direction: Annotated[
            bool, Field(description="Grow the wall outward instead of inward.")
        ] = False,
        name: Annotated[
            str | None,
            Field(min_length=1, max_length=100, description="Feature name (default 'Shell 2 mm')."),
        ] = None,
        verify: Verify = True,
        workspace_id: WorkspaceId = None,
    ) -> dict[str, Any]:
        """Hollow out a part with walls of the given thickness: remove faces to get an open
        shell (a box, a cup, an enclosure) or hollow it completely. The reply says what changed;
        the volume must drop."""
        body = features.shell_feature(
            name=name or names.shell_name(app.units.length_expression(thickness)),
            thickness=thickness,
            units=app.units,
            remove_face_ids=remove_face_ids,
            hollow_part_ids=hollow_part_ids,
            opposite_direction=opposite_direction,
        )
        return await apply_feature(app, document_id, element_id, workspace_id, body, verify=verify)

    @tool(title="Boolean (union, subtract, intersect)", writes=True)
    async def boolean(
        document_id: DocumentId,
        element_id: ElementId,
        operation: Annotated[
            features.BooleanOperation,
            Field(
                description="'union' joins the parts in `tools` into one; 'subtract' cuts the "
                "`tools` out of the `targets`; 'intersect' keeps only the volume the `tools` "
                "share."
            ),
        ],
        tools: Annotated[
            list[str],
            Field(
                min_length=1,
                description="Part IDs (list_parts): everything to union or intersect (two or "
                "more), or the cutters of a subtract.",
            ),
        ],
        targets: Annotated[
            list[str] | None,
            Field(description="Subtract only: the part(s) to cut material out of."),
        ] = None,
        keep_tools: Annotated[
            bool,
            Field(
                description="Subtract / intersect: keep the tool parts instead of consuming them."
            ),
        ] = False,
        name: Annotated[
            str | None,
            Field(min_length=1, max_length=100, description="Feature name (default 'Union')."),
        ] = None,
        verify: Verify = True,
        workspace_id: WorkspaceId = None,
    ) -> dict[str, Any]:
        """Combine whole parts: join them (union), cut one with another (subtract: a bolt hole
        shaped like a part) or keep what they share (intersect). Part IDs change when parts merge,
        so call list_parts afterwards. The reply says what changed (parts removed, volume)."""
        body = features.boolean_feature(
            name=name or names.boolean_name(operation),
            operation=operation,
            tool_ids=tools,
            target_ids=targets,
            keep_tools=keep_tools,
        )
        return await apply_feature(app, document_id, element_id, workspace_id, body, verify=verify)
