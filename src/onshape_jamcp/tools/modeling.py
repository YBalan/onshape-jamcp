"""Tools that create or change Part Studio features."""

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import BaseModel, Field

from .. import features, names, sketch
from ..app import App
from ..client import q
from ..errors import InputError, OnshapeError
from .common import DocumentId, ElementId, FeatureId, Registrar, WorkspaceId

Length = Annotated[
    float | str,
    Field(
        description="Length: a number in the configured unit (see get_settings; mm by default), a "
        "string with a unit such as '2.5 in', or an Onshape expression such as '#width'."
    ),
]
_ANGLE_SUFFIXES = ("deg", "degree", "degrees", "rad", "radian", "radians")


class FeatureUpdate(BaseModel):
    """New parameter values for one feature (used to change several features in one request)."""

    feature_id: str = Field(min_length=1, description="Feature ID from list_features.")
    parameters: dict[str, float | int | str | bool] = Field(
        min_length=1, description="Parameter ID -> new value, as for a single update."
    )


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
                description="2D shapes to draw: rectangle, circle, hexagon, line, arc or polygon. "
                "Closed shapes become regions that `extrude` can turn into solids. Sizes "
                "(diameter, radius, width, height, across_flats) may be variables such as "
                "'#shank_diameter': the sketch then follows the variable.",
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
        values: dict[str, float] = {}
        if names_used := sketch.referenced_variables(shapes):
            # One FeatureScript call. The feature list (a limited endpoint) is only read to
            # explain a failure.
            try:
                values = await app.variable_values(
                    document_id, wid, element_id, {n: "LENGTH" for n in names_used}
                )
            except OnshapeError:
                values = {}
            if unresolved := sorted(names_used - set(values)):
                defined = {v["name"] for v in await app.variables(document_id, wid, element_id)}
                missing = [n for n in unresolved if n not in defined]
                shown = ", ".join("#" + n for n in (missing or unresolved))
                raise InputError(
                    f"Variable(s) {shown} are not defined in this Part Studio. Create them first "
                    "with set_variable."
                    if missing
                    else f"Variable(s) {shown} are not lengths; sketch sizes must be lengths."
                )
        body = sketch.build_sketch_feature(name, plane_id, shapes, app.units, values)
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
        name: Annotated[
            str | None,
            Field(
                min_length=1,
                max_length=100,
                description="Feature name. Defaults to its size: 'Extrude 8 mm' ('Cut 8 mm' for "
                "operation 'remove', 'Hole' with hole=true); with a variable depth the name "
                "shows the variable, e.g. 'Extrude (head_h)'. Do not put '#word' in a name: "
                "Onshape reads it as a parameter of the feature and shows '?'.",
            ),
        ] = None,
        hole: Annotated[
            bool,
            Field(
                description="The cut is a round drilled hole (use operation 'remove'): it is "
                "named 'Hole', without a size."
            ),
        ] = False,
        only_parts: Annotated[
            list[str] | None,
            Field(
                description="Part IDs (from list_parts) that operations 'add', 'remove' and "
                "'intersect' should affect. By default Onshape applies them to every part the "
                "extrusion touches, which matters when several parts share one Part Studio "
                "(e.g. a bolt and a nut)."
            ),
        ] = None,
        part_name: Annotated[
            str | None,
            Field(
                min_length=1,
                max_length=200,
                description="Name for the part an operation 'new' creates, e.g. 'M10x40 Hex "
                "Bolt'. Give one unless the user chose a name: otherwise the part stays "
                "'Part N'.",
            ),
        ] = None,
        workspace_id: WorkspaceId = None,
    ) -> dict[str, Any]:
        """Extrude all closed regions of a sketch into a solid, or cut with it (operation
        'remove'). The default direction follows the sketch plane's normal. For a sketch on a
        part's own face that normal points OUT of the part, so to cut a hole or pocket INTO the
        part pass opposite_direction=true (verified on live Onshape; without it nothing is
        cut). Always confirm with get_part_properties - the volume must drop. For operation 'new'
        the reply lists the created parts (parts_created) and names them when part_name is
        given."""
        wid = await app.workspace(document_id, workspace_id)
        existing = (
            await app.parts(document_id, wid, element_id)
            if operation in ("new", "intersect")
            else []
        )
        if operation == "intersect" and not only_parts and len(existing) > 1:
            # Verified live: an unscoped intersect deleted a part that did not overlap the tool.
            raise InputError(
                f"This Part Studio has {len(existing)} parts and an unscoped 'intersect' deletes "
                "every part that does not overlap the new body. Pass only_parts with the part "
                "IDs to intersect (from list_parts)."
            )
        before = {p.get("partId") for p in existing} if operation == "new" else None
        depth_text = app.units.length_expression(depth)
        body = features.extrude_feature(
            name=name or names.extrude_name(operation, depth_text, symmetric=symmetric, hole=hole),
            sketch_feature_id=sketch_feature_id,
            depth=depth,
            operation=operation,
            units=app.units,
            opposite_direction=opposite_direction,
            symmetric=symmetric,
            only_parts=only_parts,
        )
        result = await add(document_id, element_id, wid, body)
        if before is not None:
            created, note = await app.name_new_parts(
                document_id, wid, element_id, before, part_name
            )
            result["parts_created"] = created
            if note:
                result["note"] = note
        return result

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
        feature_id: Annotated[
            str | None,
            Field(
                min_length=1, description="Feature ID (list_features) when changing one feature."
            ),
        ] = None,
        parameters: Annotated[
            dict[str, float | int | str | bool] | None,
            Field(
                min_length=1,
                description="Map of parameter ID to new value, e.g. {'depth': 25} for an extrude "
                "or {'radius': 2} for a fillet. Numbers are lengths in the configured unit "
                "(degrees for angle parameters). Strings may carry units ('1 in') or be "
                "expressions ('#width'). Booleans and enum names are set as-is. Use "
                "list_features with feature_id to see a feature's parameter IDs.",
            ),
        ] = None,
        name: Annotated[str | None, Field(description="Optional new feature name.")] = None,
        updates: Annotated[
            list[FeatureUpdate] | None,
            Field(
                min_length=1,
                description="Change SEVERAL features in ONE Onshape request instead of "
                "feature_id + parameters. Names are not changed in this form.",
            ),
        ] = None,
        workspace_id: WorkspaceId = None,
    ) -> dict[str, Any]:
        """Change parameters of existing features (for example make an extrude deeper or a
        fillet larger) without rebuilding them. Dependent features rebuild automatically. To
        change several features at once pass `updates`: two requests in total, however many.
        An automatically named extrude ('Extrude 8 mm') gets its name refreshed when its depth
        changes (single-feature form)."""
        if updates:
            if feature_id or parameters or name:
                raise InputError("Use either `updates` or feature_id + parameters, not both.")
        elif not (feature_id and parameters):
            raise InputError("Give feature_id and parameters, or `updates`.")
        wid = await app.workspace(document_id, workspace_id)
        path = _features_path(document_id, wid, element_id)
        data = await app.client.get(path)
        by_feature = {f.get("featureId"): f for f in data.get("features") or []}

        def apply(feature_id: str, values: dict[str, Any]) -> tuple[dict, list[dict]]:
            feature = by_feature.get(feature_id)
            if feature is None:
                raise InputError(
                    f"No feature with ID {feature_id!r}. Call list_features to see IDs."
                )
            by_id = {
                p["parameterId"]: p for p in feature.get("parameters") or [] if "parameterId" in p
            }
            changed = []
            for parameter_id, value in values.items():
                param = by_id.get(parameter_id)
                if param is None:
                    raise InputError(
                        f"Feature {feature_id} has no parameter {parameter_id!r}. "
                        f"Available: {sorted(by_id)}."
                    )
                _set_parameter(param, parameter_id, value, app)
                changed.append(param)
            return feature, changed

        if updates:
            ids = [u.feature_id for u in updates]
            if len(set(ids)) != len(ids):
                raise InputError("List each feature once in `updates`.")
            structs = []
            for u in updates:
                _, changed = apply(u.feature_id, u.parameters)
                # Checked live: only the changed parameters are needed; a name sent here is ignored.
                structs.append(
                    {"btType": "BTMFeature-134", "featureId": u.feature_id, "parameters": changed}
                )
            response = await app.client.post(
                f"{path}/updates",
                {
                    "btType": "BTUpdateFeaturesCall-1748",
                    "features": structs,
                    "updateSuppressionAttributes": False,
                },
            )
            states = response.get("featureStates") or {}
            return {
                "updated": [
                    {
                        "feature_id": fid,
                        "name": by_feature[fid].get("name"),
                        "status": (states.get(fid) or {}).get("featureStatus", "UNKNOWN"),
                    }
                    for fid in ids
                ],
                "onshape_requests": 2,
            }

        assert feature_id and parameters
        feature, _ = apply(feature_id, parameters)
        by_id = {p.get("parameterId"): p for p in feature.get("parameters") or []}
        if name:
            feature["name"] = name
        elif (
            feature.get("featureType") == "extrude"
            and "depth" in parameters
            and names.is_auto_extrude_name(feature.get("name"))
        ):  # keep "Extrude 8 mm" truthful when the depth changes
            removing = (by_id.get("operationType") or {}).get("value") == "REMOVE"
            symmetric = (by_id.get("endBound") or {}).get("value") == "SYMMETRIC"
            feature["name"] = names.extrude_name(
                "remove" if removing else "add",
                str(by_id["depth"].get("expression")),
                symmetric=symmetric,
            )
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
            if param.get("isInteger"):  # a count (e.g. thread turns), not a length
                expression = app.units.number_expression(value)
            elif is_angle:
                expression = app.units.angle_expression(value)
            else:
                expression = app.units.length_expression(value)
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
