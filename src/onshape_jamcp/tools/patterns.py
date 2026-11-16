"""Pattern and mirror features: repeat parts, features or faces."""

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from .. import features, names
from ..app import App
from ..errors import InputError
from .applying import Verify, apply_feature
from .common import DocumentId, ElementId, Registrar, WorkspaceId
from .modeling import Length

MAX_INSTANCES = 1000

Count = Annotated[
    int | str,
    Field(
        description="Number of instances INCLUDING the original (2 or more), or an Onshape "
        "expression such as '#spokes' so a variable or configuration can drive it."
    ),
]
PartIds = Annotated[
    list[str] | None,
    Field(
        description="Repeat these whole parts (IDs from list_parts). Give exactly one of "
        "part_ids, feature_ids and face_ids."
    ),
]
FeatureIds = Annotated[
    list[str] | None,
    Field(
        description="Repeat these features (feature_id of an extrude or other feature, e.g. one "
        "spoke of a wheel); each copy merges the way the feature does. Give exactly one of "
        "part_ids, feature_ids and face_ids."
    ),
]
FaceIds = Annotated[
    list[str] | None,
    Field(
        description="Repeat these faces (IDs from get_body_details). Give exactly one of "
        "part_ids, feature_ids and face_ids."
    ),
]
PatternOperation = Annotated[
    features.Operation,
    Field(
        description="Only for part_ids: 'new' makes each copy a separate part (default); 'add' "
        "merges the copies into existing parts; 'remove' cuts with them; 'intersect' keeps the "
        "overlap."
    ),
]
MergeWith = Annotated[
    list[str] | None,
    Field(
        description="Part IDs the copies merge with when operation is 'add', 'remove' or "
        "'intersect' (default: every part they touch)."
    ),
]
FeatureName = Annotated[str | None, Field(min_length=1, max_length=100)]


def _check_count(count: int | str, what: str = "count") -> int | str:
    if isinstance(count, str):
        return count
    if isinstance(count, bool) or int(count) != count or not 2 <= count <= MAX_INSTANCES:
        raise InputError(
            f"{what} must be a whole number from 2 to {MAX_INSTANCES} (it includes the "
            f"original), or an expression such as '#spokes'; got {count!r}."
        )
    return int(count)


def register(mcp: MCPServer, app: App, tool: Registrar) -> None:
    @tool(title="Circular pattern", writes=True)
    async def circular_pattern(
        document_id: DocumentId,
        element_id: ElementId,
        count: Count,
        axis: Annotated[
            str,
            Field(
                min_length=1,
                description="The axis to turn about: the ID of a cylindrical or conical face or "
                "of a circular or straight edge (from get_body_details), or 'sketch:<feature_id>' "
                "to use the construction line drawn in that sketch (create_sketch with a `line` "
                "shape and construction=true).",
            ),
        ],
        part_ids: PartIds = None,
        feature_ids: FeatureIds = None,
        face_ids: FaceIds = None,
        angle: Annotated[
            float | str,
            Field(
                description="Total angle in degrees (default 360: a full circle, instances "
                "360/count apart). With equal_spacing=false it is the angle BETWEEN instances."
            ),
        ] = 360,
        equal_spacing: Annotated[
            bool, Field(description="Spread the instances equally over `angle`.")
        ] = True,
        opposite_direction: Annotated[bool, Field(description="Turn the other way.")] = False,
        operation: PatternOperation = "new",
        merge_with: MergeWith = None,
        name: FeatureName = None,
        verify: Verify = True,
        workspace_id: WorkspaceId = None,
    ) -> dict[str, Any]:
        """Repeat parts, features or faces around an axis (a wheel's spokes, bolt holes on a
        circle). Pattern the FEATURE that makes one spoke or hole (feature_ids) so the copies
        merge or cut as it does; pattern parts (part_ids) to get separate copies. Put the count
        in a variable ('#spokes') when it should be configurable. The default name shows the
        count ('Circular pattern x6'). The reply says what changed (parts, volume)."""
        count = _check_count(count)
        wid = await app.workspace(document_id, workspace_id)
        axis_id = await app.reference_id(document_id, wid, element_id, axis, what="axis")
        angle_text = app.units.angle_expression(angle)
        body = features.circular_pattern_feature(
            name=name
            or names.circular_pattern_name(app.units.number_expression(count), angle_text),
            axis_id=axis_id,
            count=count,
            angle=angle,
            units=app.units,
            part_ids=part_ids,
            feature_ids=feature_ids,
            face_ids=face_ids,
            operation=operation,
            merge_with=merge_with,
            equal_spacing=equal_spacing,
            opposite_direction=opposite_direction,
        )
        return await apply_feature(app, document_id, element_id, wid, body, verify=verify)

    @tool(title="Linear pattern", writes=True)
    async def linear_pattern(
        document_id: DocumentId,
        element_id: ElementId,
        count: Count,
        distance: Length,
        direction: Annotated[
            str,
            Field(
                min_length=1,
                description="Direction of the row: 'x', 'y' or 'z' (the world axes), the ID of a "
                "straight edge, planar face or cylindrical face (from get_body_details; a face "
                "gives its normal), or 'sketch:<feature_id>' for the construction line of a "
                "sketch.",
            ),
        ],
        part_ids: PartIds = None,
        feature_ids: FeatureIds = None,
        face_ids: FaceIds = None,
        opposite_direction: Annotated[bool, Field(description="Go the other way.")] = False,
        count_2: Annotated[
            int | str | None,
            Field(
                description="Second direction (a grid): number of rows. Needs direction_2 and "
                "distance_2 too."
            ),
        ] = None,
        distance_2: Annotated[
            float | str | None, Field(description="Second direction: spacing between rows.")
        ] = None,
        direction_2: Annotated[
            str | None, Field(description="Second direction, same forms as `direction`.")
        ] = None,
        opposite_direction_2: Annotated[
            bool, Field(description="Second direction: go the other way.")
        ] = False,
        operation: PatternOperation = "new",
        merge_with: MergeWith = None,
        name: FeatureName = None,
        verify: Verify = True,
        workspace_id: WorkspaceId = None,
    ) -> dict[str, Any]:
        """Repeat parts, features or faces in a straight line (a row of holes, ribs, legs); add
        a second direction for a grid. `distance` is the spacing between neighbouring instances.
        Pattern the FEATURE that makes one hole or rib (feature_ids) so the copies cut or merge as
        it does. The default name shows count and spacing ('Linear pattern x4 @ 20 mm'). The
        reply says what changed (parts, volume)."""
        count = _check_count(count)
        second_given = [v is not None for v in (count_2, distance_2, direction_2)]
        if any(second_given) and not all(second_given):
            raise InputError("A second direction needs count_2, distance_2 and direction_2.")
        wid = await app.workspace(document_id, workspace_id)
        direction_id, flip = await app.direction(document_id, wid, element_id, direction)
        second = None
        if direction_2 is not None:
            assert count_2 is not None and distance_2 is not None
            direction_2_id, flip_2 = await app.direction(document_id, wid, element_id, direction_2)
            second = {
                "direction_id": direction_2_id,
                "count": _check_count(count_2, "count_2"),
                "distance": distance_2,
                "opposite_direction": flip_2 != opposite_direction_2,
            }
        units = app.units
        body = features.linear_pattern_feature(
            name=name
            or names.linear_pattern_name(
                units.number_expression(count),
                units.length_expression(distance),
                (units.number_expression(second["count"]), units.length_expression(distance_2))
                if second and distance_2 is not None
                else None,
            ),
            direction_id=direction_id,
            count=count,
            distance=distance,
            units=units,
            part_ids=part_ids,
            feature_ids=feature_ids,
            face_ids=face_ids,
            operation=operation,
            merge_with=merge_with,
            opposite_direction=flip != opposite_direction,
            second=second,
        )
        return await apply_feature(app, document_id, element_id, wid, body, verify=verify)

    @tool(title="Mirror", writes=True)
    async def mirror(
        document_id: DocumentId,
        element_id: ElementId,
        plane: Annotated[
            str,
            Field(
                min_length=1,
                description="Mirror plane: 'Top', 'Front' or 'Right' (the origin planes) or the "
                "ID of a planar face (from get_body_details).",
            ),
        ],
        part_ids: PartIds = None,
        feature_ids: FeatureIds = None,
        face_ids: FaceIds = None,
        operation: PatternOperation = "new",
        merge_with: MergeWith = None,
        name: FeatureName = None,
        verify: Verify = True,
        workspace_id: WorkspaceId = None,
    ) -> dict[str, Any]:
        """Mirror parts, features or faces across a plane. Mirroring a part gives a second
        part (operation 'new') or merges the copy into the original ('add'); mirroring the
        feature that makes one half builds a symmetric part. The default name shows the plane
        ('Mirror (Right)'). The reply says what changed (parts, volume)."""
        wid = await app.workspace(document_id, workspace_id)
        plane_id = await app.plane_id(document_id, wid, element_id, plane)
        body = features.mirror_feature(
            name=name or names.mirror_name(plane.strip()),
            plane_id=plane_id,
            part_ids=part_ids,
            feature_ids=feature_ids,
            face_ids=face_ids,
            operation=operation,
            merge_with=merge_with,
        )
        return await apply_feature(app, document_id, element_id, wid, body, verify=verify)
