"""Tools that create or change Part Studio features."""

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from .. import features, sketch
from ..app import App
from ..client import q
from ..errors import InputError
from .common import DocumentId, ElementId, FeatureId, Registrar, WorkspaceId

Length = Annotated[
    float | str,
    Field(
        description="Length: a number in the configured unit (see get_settings; mm by default), a "
        "string with a unit such as '2.5 in', or an Onshape expression such as '#width'."
    ),
]
_ANGLE_SUFFIXES = ("deg", "degree", "degrees", "rad", "radian", "radians")


def _features_path(document_id: str, workspace_id: str, element_id: str) -> str:
    return f"/partstudios/d/{q(document_id)}/w/{q(workspace_id)}/e/{q(element_id)}/features"


def register(mcp: MCPServer, app: App, tool: Registrar) -> None:
    async def add(document_id: str, element_id: str, workspace_id: str | None, body: dict) -> dict:
        wid = await app.workspace(document_id, workspace_id)
        response = await app.client.post(_features_path(document_id, wid, element_id), body)
        return features.summarize_feature_response(response)

    @tool(title="Create sketch", writes=True)
    async def create_sketch(
        document_id: DocumentId,
        element_id: ElementId,
        shapes: Annotated[
            list[sketch.Shape],
            Field(
                min_length=1,
                max_length=sketch.MAX_SHAPES,
                description="2D shapes to draw: rectangle, circle, line, arc or polygon. Closed "
                "shapes become regions that `extrude` can turn into solids.",
            ),
        ],
        plane: Annotated[
            str,
            Field(
                description="'Top' (XY plane), 'Front' (XZ plane) or 'Right' (YZ plane) of the "
                "origin, or the ID of a planar face / plane from get_body_details to sketch "
                "on an existing face."
            ),
        ] = "Top",
        name: Annotated[str, Field(min_length=1, max_length=100)] = "Sketch",
        workspace_id: WorkspaceId = None,
    ) -> dict[str, Any]:
        """Create a sketch of 2D shapes on a plane or planar face. Point coordinates are [x, y]
        in the sketch's own axes, in the configured length unit (mm by default). Returns the
        sketch's feature_id, to pass to `extrude`. Tip: verify placement afterwards with
        get_part_properties or a screenshot."""
        wid = await app.workspace(document_id, workspace_id)
        plane_id = await app.plane_id(document_id, wid, element_id, plane)
        body = sketch.build_sketch_feature(name, plane_id, shapes, app.units)
        result = await add(document_id, element_id, wid, body)
        result["plane"] = plane
        return result

    @tool(title="Extrude sketch", writes=True)
    async def extrude(
        document_id: DocumentId,
        element_id: ElementId,
        sketch_feature_id: Annotated[
            str, Field(min_length=1, description="feature_id returned by create_sketch.")
        ],
        depth: Length,
        operation: Annotated[
            features.Operation,
            Field(
                description="'new' creates a separate part; 'add' merges into existing parts; "
                "'remove' cuts material away (holes, pockets); 'intersect' keeps only the overlap."
            ),
        ] = "new",
        symmetric: Annotated[
            bool, Field(description="Extrude half the depth to each side of the sketch plane.")
        ] = False,
        opposite_direction: Annotated[
            bool, Field(description="Flip to the other side of the sketch plane.")
        ] = False,
        name: Annotated[str, Field(min_length=1, max_length=100)] = "Extrude",
        workspace_id: WorkspaceId = None,
    ) -> dict[str, Any]:
        """Extrude all closed regions of a sketch into a solid, or cut with it (operation
        'remove'). The default direction follows the sketch plane's normal. For a sketch on a
        part's own face that normal points OUT of the part, so to cut a hole or pocket INTO the
        part pass opposite_direction=true (verified on live Onshape; without it nothing is
        cut). Always confirm with get_part_properties - the volume must drop."""
        body = features.extrude_feature(
            name=name,
            sketch_feature_id=sketch_feature_id,
            depth=depth,
            operation=operation,
            units=app.units,
            opposite_direction=opposite_direction,
            symmetric=symmetric,
        )
        return await add(document_id, element_id, workspace_id, body)

    @tool(title="Fillet edges", writes=True)
    async def fillet(
        document_id: DocumentId,
        element_id: ElementId,
        edge_ids: Annotated[
            list[str],
            Field(min_length=1, description="Edge IDs from get_body_details to round."),
        ],
        radius: Length,
        name: Annotated[str, Field(min_length=1, max_length=100)] = "Fillet",
        workspace_id: WorkspaceId = None,
    ) -> dict[str, Any]:
        """Round the given edges with a constant-radius fillet."""
        body = features.fillet_feature(name=name, edge_ids=edge_ids, radius=radius, units=app.units)
        return await add(document_id, element_id, workspace_id, body)

    @tool(title="Chamfer edges", writes=True)
    async def chamfer(
        document_id: DocumentId,
        element_id: ElementId,
        edge_ids: Annotated[
            list[str],
            Field(min_length=1, description="Edge IDs from get_body_details to bevel."),
        ],
        distance: Length,
        name: Annotated[str, Field(min_length=1, max_length=100)] = "Chamfer",
        workspace_id: WorkspaceId = None,
    ) -> dict[str, Any]:
        """Bevel the given edges with an equal-distance chamfer."""
        body = features.chamfer_feature(
            name=name, edge_ids=edge_ids, distance=distance, units=app.units
        )
        return await add(document_id, element_id, workspace_id, body)

    @tool(title="Update feature parameters", writes=True, idempotent=True)
    async def update_feature(
        document_id: DocumentId,
        element_id: ElementId,
        feature_id: FeatureId,
        parameters: Annotated[
            dict[str, float | int | str | bool],
            Field(
                min_length=1,
                description="Map of parameter ID to new value, e.g. {'depth': 25} for an extrude "
                "or {'radius': 2} for a fillet. Numbers are lengths in the configured unit "
                "(degrees for angle parameters). Strings may carry units ('1 in') or be "
                "expressions ('#width'). Booleans and enum names are set as-is. Use "
                "list_features with feature_id to see a feature's parameter IDs.",
            ),
        ],
        name: Annotated[str | None, Field(description="Optional new feature name.")] = None,
        workspace_id: WorkspaceId = None,
    ) -> dict[str, Any]:
        """Change parameters of an existing feature (for example make an extrude deeper or a
        fillet larger) without rebuilding it. Dependent features rebuild automatically."""
        wid = await app.workspace(document_id, workspace_id)
        path = _features_path(document_id, wid, element_id)
        data = await app.client.get(path)
        feature = next(
            (f for f in data.get("features") or [] if f.get("featureId") == feature_id), None
        )
        if feature is None:
            raise InputError(f"No feature with ID {feature_id!r}. Call list_features to see IDs.")
        by_id = {p["parameterId"]: p for p in feature.get("parameters") or [] if "parameterId" in p}
        for parameter_id, value in parameters.items():
            param = by_id.get(parameter_id)
            if param is None:
                raise InputError(
                    f"Feature {feature_id} has no parameter {parameter_id!r}. "
                    f"Available: {sorted(by_id)}."
                )
            _set_parameter(param, parameter_id, value, app)
        if name:
            feature["name"] = name
        response = await app.client.post(
            f"{path}/featureid/{q(feature_id)}",
            {"btType": "BTFeatureDefinitionCall-1406", "feature": feature},
        )
        return features.summarize_feature_response(response)

    @tool(title="Delete feature", writes=True, destructive=True)
    async def delete_feature(
        document_id: DocumentId,
        element_id: ElementId,
        feature_id: FeatureId,
        workspace_id: WorkspaceId = None,
    ) -> dict[str, Any]:
        """Permanently remove a feature from the Part Studio. Features that depend on it (for
        example an extrude that uses a deleted sketch) will fail. Onshape's version history can
        restore it."""
        wid = await app.workspace(document_id, workspace_id)
        await app.client.delete(
            f"{_features_path(document_id, wid, element_id)}/featureid/{q(feature_id)}"
        )
        return {"deleted": feature_id}

    @tool(title="Add raw feature (advanced)", writes=True)
    async def add_feature(
        document_id: DocumentId,
        element_id: ElementId,
        feature: Annotated[
            dict[str, Any],
            Field(
                description="Onshape feature JSON (a BTMFeature-134 object with featureType, name "
                "and parameters). A full BTFeatureDefinitionCall body is accepted too."
            ),
        ],
        workspace_id: WorkspaceId = None,
    ) -> dict[str, Any]:
        """Advanced escape hatch: add any Onshape feature from raw JSON - revolve, patterns,
        sweeps, holes, and so on. Copy the structure from an existing feature
        (`list_features` with `feature_id`) or from `onshape_api_request` on the Part Studio
        `featurespecs` endpoint. Raw JSON uses Onshape's native units (lengths are written as
        expressions like '10 mm')."""
        if not isinstance(feature, dict) or not feature:
            raise InputError("feature must be a non-empty JSON object.")
        body = (
            feature
            if isinstance(feature.get("feature"), dict)
            else {
                "btType": "BTFeatureDefinitionCall-1406",
                "feature": feature,
            }
        )
        return await add(document_id, element_id, workspace_id, body)


def _set_parameter(param: dict[str, Any], parameter_id: str, value: Any, app: App) -> None:
    """Apply ``value`` to a feature parameter dict in place, respecting its Onshape type."""
    kind = str(param.get("btType", ""))
    if kind.startswith("BTMParameterQuantity"):
        if isinstance(value, bool):
            raise InputError(f"{parameter_id}: expected a number or expression, got a boolean.")
        current = str(param.get("expression") or "").strip().lower()
        is_angle = current.endswith(_ANGLE_SUFFIXES)
        if isinstance(value, int | float):
            expression = (
                app.units.angle_expression(value)
                if is_angle
                else app.units.length_expression(value)
            )
        else:
            expression = app.units.length_expression(value)
        param["expression"] = expression
        param.pop("value", None)
    elif kind.startswith("BTMParameterBoolean"):
        if not isinstance(value, bool):
            raise InputError(f"{parameter_id}: expected true or false.")
        param["value"] = value
    elif kind.startswith(("BTMParameterEnum", "BTMParameterString")):
        if not isinstance(value, str):
            raise InputError(f"{parameter_id}: expected a string.")
        param["value"] = value
    else:
        raise InputError(
            f"{parameter_id}: parameters of type {kind or 'unknown'} cannot be edited with "
            f"update_feature. Delete and re-create the feature instead."
        )
