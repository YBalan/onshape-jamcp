"""Builders for Part Studio features (extrude, revolve, patterns, mirror, shell, boolean, fillet,
chamfer) and response helpers.

The parameter IDs and enum names come from Onshape's own feature specs
(``GET .../featurespecs``), so they are exact; whether Onshape *acts* on them is checked by
measuring the Part Studio (see ``describe_effect``), because an accepted feature can do nothing.
"""

import math
from typing import Any, Literal

from .errors import InputError
from .units import UnitSystem

Operation = Literal["new", "add", "remove", "intersect"]
_OPERATIONS = {"new": "NEW", "add": "ADD", "remove": "REMOVE", "intersect": "INTERSECT"}


# --- parameter primitives -------------------------------------------------------------------


def query_param(parameter_id: str, ids: list[str]) -> dict[str, Any]:
    if not ids:
        raise InputError(f"{parameter_id}: at least one ID is required.")
    return {
        "btType": "BTMParameterQueryList-148",
        "queries": [{"btType": "BTMIndividualQuery-138", "deterministicIds": list(ids)}],
        "parameterId": parameter_id,
    }


def sketch_region_param(parameter_id: str, sketch_feature_id: str) -> dict[str, Any]:
    """Select every closed region of a sketch."""
    return {
        "btType": "BTMParameterQueryList-148",
        "queries": [
            {
                "btType": "BTMIndividualSketchRegionQuery-140",
                "featureId": sketch_feature_id,
                "filterInnerLoops": True,
                "queryString": f'query = qSketchRegion(id + "{sketch_feature_id}", true);',
            }
        ],
        "parameterId": parameter_id,
    }


def quantity_param(parameter_id: str, expression: str, *, integer: bool = False) -> dict[str, Any]:
    return {
        "btType": "BTMParameterQuantity-147",
        "isInteger": integer,
        "expression": expression,
        "parameterId": parameter_id,
    }


def feature_list_param(parameter_id: str, feature_ids: list[str]) -> dict[str, Any]:
    """A list of features (what a *feature* pattern repeats)."""
    return {
        "btType": "BTMParameterFeatureList-1749",
        "featureIds": list(feature_ids),
        "parameterId": parameter_id,
    }


def enum_param(parameter_id: str, enum_name: str, value: str) -> dict[str, Any]:
    return {
        "btType": "BTMParameterEnum-145",
        "enumName": enum_name,
        "value": value,
        "parameterId": parameter_id,
    }


def bool_param(parameter_id: str, value: bool) -> dict[str, Any]:
    return {"btType": "BTMParameterBoolean-144", "value": value, "parameterId": parameter_id}


def wrap_feature(feature_type: str, name: str, parameters: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "btType": "BTFeatureDefinitionCall-1406",
        "feature": {
            "btType": "BTMFeature-134",
            "featureType": feature_type,
            "name": name,
            "suppressed": False,
            "namespace": "",
            "parameters": parameters,
        },
    }


# --- feature builders -----------------------------------------------------------------------


def extrude_feature(
    *,
    name: str,
    sketch_feature_id: str,
    depth: float | int | str,
    operation: Operation,
    units: UnitSystem,
    opposite_direction: bool = False,
    symmetric: bool = False,
    only_parts: list[str] | None = None,
) -> dict[str, Any]:
    if operation not in _OPERATIONS:
        raise InputError(f"operation must be one of {sorted(_OPERATIONS)}, got {operation!r}.")
    parameters = [
        sketch_region_param("entities", sketch_feature_id),
        enum_param("operationType", "NewBodyOperationType", _OPERATIONS[operation]),
    ]
    if symmetric:
        parameters.append(enum_param("endBound", "BoundingType", "SYMMETRIC"))
    parameters += [
        quantity_param("depth", units.length_expression(depth)),
        bool_param("oppositeDirection", opposite_direction),
    ]
    if only_parts:  # restrict add / remove / intersect to these parts instead of every part hit
        parameters += [bool_param("defaultScope", False), query_param("booleanScope", only_parts)]
    return wrap_feature("extrude", name, parameters)


def _scope(operation: str, merge_with: list[str] | None) -> list[dict[str, Any]]:
    """Which parts an add / remove / intersect operation may touch (Onshape's default: all)."""
    if operation != "new" and merge_with:
        return [bool_param("defaultScope", False), query_param("booleanScope", merge_with)]
    return []


def _checked_operation(operation: str) -> str:
    if operation not in _OPERATIONS:
        raise InputError(f"operation must be one of {sorted(_OPERATIONS)}, got {operation!r}.")
    return _OPERATIONS[operation]


def _selection(
    enum_name: str,
    *,
    part_ids: list[str] | None,
    feature_ids: list[str] | None,
    face_ids: list[str] | None,
    operation: str,
    merge_with: list[str] | None,
) -> list[dict[str, Any]]:
    """What a pattern or mirror repeats: whole parts, features, or faces - exactly one of them."""
    given = [
        kind
        for kind, ids in (
            ("part_ids", part_ids),
            ("feature_ids", feature_ids),
            ("face_ids", face_ids),
        )
        if ids
    ]
    if len(given) != 1:
        raise InputError(
            "Say what to repeat with exactly one of part_ids (whole parts, from list_parts), "
            "feature_ids (features such as an extrude, from list_features or a create tool) or "
            f"face_ids (from get_body_details); got {', '.join(given) or 'none'}."
        )
    if part_ids:
        return [
            enum_param("patternType", enum_name, "PART"),
            enum_param("operationType", "NewBodyOperationType", _checked_operation(operation)),
            query_param("entities", part_ids),
            *_scope(operation, merge_with),
        ]
    if feature_ids:
        # Checked live: without a *full* pattern Onshape's "faster" mode answers ERROR for a
        # feature that merges into an existing part (a spoke added to a hub); with it the copies
        # come out right.
        return [
            enum_param("patternType", enum_name, "FEATURE"),
            feature_list_param("instanceFunction", feature_ids),
            bool_param("fullFeaturePattern", True),
        ]
    assert face_ids
    return [enum_param("patternType", enum_name, "FACE"), query_param("faces", face_ids)]


def circular_pattern_feature(
    *,
    name: str,
    axis_id: str,
    count: float | int | str,
    angle: float | int | str,
    units: UnitSystem,
    part_ids: list[str] | None = None,
    feature_ids: list[str] | None = None,
    face_ids: list[str] | None = None,
    operation: str = "new",
    merge_with: list[str] | None = None,
    equal_spacing: bool = True,
    opposite_direction: bool = False,
) -> dict[str, Any]:
    return wrap_feature(
        "circularPattern",
        name,
        [
            *_selection(
                "PatternType",
                part_ids=part_ids,
                feature_ids=feature_ids,
                face_ids=face_ids,
                operation=operation,
                merge_with=merge_with,
            ),
            query_param("axis", [axis_id]),
            quantity_param("angle", units.angle_expression(angle)),
            quantity_param("instanceCount", units.number_expression(count), integer=True),
            bool_param("oppositeDirection", opposite_direction),
            bool_param("equalSpace", equal_spacing),
        ],
    )


def linear_pattern_feature(
    *,
    name: str,
    direction_id: str,
    count: float | int | str,
    distance: float | int | str,
    units: UnitSystem,
    part_ids: list[str] | None = None,
    feature_ids: list[str] | None = None,
    face_ids: list[str] | None = None,
    operation: str = "new",
    merge_with: list[str] | None = None,
    opposite_direction: bool = False,
    second: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """``second`` adds a second direction: ``direction_id``, ``count``, ``distance`` and
    ``opposite_direction`` of the second row of instances."""
    parameters = [
        *_selection(
            "PatternType",
            part_ids=part_ids,
            feature_ids=feature_ids,
            face_ids=face_ids,
            operation=operation,
            merge_with=merge_with,
        ),
        query_param("directionOne", [direction_id]),
        quantity_param("distance", units.length_expression(distance)),
        quantity_param("instanceCount", units.number_expression(count), integer=True),
        bool_param("oppositeDirection", opposite_direction),
    ]
    if second:
        parameters += [
            bool_param("hasSecondDir", True),
            query_param("directionTwo", [second["direction_id"]]),
            quantity_param("distanceTwo", units.length_expression(second["distance"])),
            quantity_param(
                "instanceCountTwo", units.number_expression(second["count"]), integer=True
            ),
            bool_param("oppositeDirectionTwo", bool(second.get("opposite_direction"))),
        ]
    return wrap_feature("linearPattern", name, parameters)


def mirror_feature(
    *,
    name: str,
    plane_id: str,
    part_ids: list[str] | None = None,
    feature_ids: list[str] | None = None,
    face_ids: list[str] | None = None,
    operation: str = "new",
    merge_with: list[str] | None = None,
) -> dict[str, Any]:
    return wrap_feature(
        "mirror",
        name,
        [
            *_selection(
                "MirrorType",
                part_ids=part_ids,
                feature_ids=feature_ids,
                face_ids=face_ids,
                operation=operation,
                merge_with=merge_with,
            ),
            query_param("mirrorPlane", [plane_id]),
        ],
    )


def revolve_feature(
    *,
    name: str,
    sketch_feature_id: str,
    axis_id: str,
    angle: float | int | str,
    operation: Operation,
    units: UnitSystem,
    symmetric: bool = False,
    opposite_direction: bool = False,
    only_parts: list[str] | None = None,
) -> dict[str, Any]:
    angle_text = units.angle_expression(angle)
    full = angle_text.replace(" deg", "") == "360"
    parameters = [
        sketch_region_param("entities", sketch_feature_id),
        enum_param("operationType", "NewBodyOperationType", _checked_operation(operation)),
        query_param("axis", [axis_id]),
        bool_param("fullRevolve", full),
    ]
    if not full:
        parameters += [
            quantity_param("angle", angle_text),
            bool_param("symmetric", symmetric),
        ]
    parameters.append(bool_param("oppositeDirection", opposite_direction))
    parameters += _scope(operation, only_parts)
    return wrap_feature("revolve", name, parameters)


def shell_feature(
    *,
    name: str,
    thickness: float | int | str,
    units: UnitSystem,
    remove_face_ids: list[str] | None = None,
    hollow_part_ids: list[str] | None = None,
    opposite_direction: bool = False,
) -> dict[str, Any]:
    """Shell the faces away (open box) or hollow whole parts (closed void)."""
    if bool(remove_face_ids) == bool(hollow_part_ids):
        raise InputError(
            "Give exactly one of remove_face_ids (faces to open, from get_body_details) or "
            "hollow_part_ids (parts to hollow out completely, from list_parts)."
        )
    if remove_face_ids:
        selection = [bool_param("isHollow", False), query_param("entities", remove_face_ids)]
    else:
        assert hollow_part_ids
        selection = [bool_param("isHollow", True), query_param("parts", hollow_part_ids)]
    return wrap_feature(
        "shell",
        name,
        [
            *selection,
            quantity_param("thickness", units.length_expression(thickness)),
            bool_param("oppositeDirection", opposite_direction),
        ],
    )


BooleanOperation = Literal["union", "subtract", "intersect"]
_BOOLEAN = {"union": "UNION", "subtract": "SUBTRACTION", "intersect": "INTERSECTION"}


def boolean_feature(
    *,
    name: str,
    operation: BooleanOperation,
    tool_ids: list[str],
    target_ids: list[str] | None = None,
    keep_tools: bool = False,
) -> dict[str, Any]:
    if operation not in _BOOLEAN:
        raise InputError(f"operation must be one of {sorted(_BOOLEAN)}, got {operation!r}.")
    if len(tool_ids) < (1 if operation == "subtract" else 2):
        raise InputError(
            "A subtract needs the cutter part(s) in `tools`; a union or intersect needs at least "
            "two parts in `tools`."
        )
    if operation == "subtract" and not target_ids:
        raise InputError("A subtract needs `targets`: the part(s) to cut material out of.")
    if operation != "subtract" and target_ids:
        raise InputError("`targets` is only used by 'subtract'; put every part in `tools`.")
    parameters = [
        enum_param("operationType", "BooleanOperationType", _BOOLEAN[operation]),
        query_param("tools", tool_ids),
    ]
    if target_ids:
        parameters.append(query_param("targets", target_ids))
    if keep_tools:
        parameters.append(bool_param("keepTools", True))
    return wrap_feature("booleanBodies", name, parameters)


def fillet_feature(
    *, name: str, edge_ids: list[str], radius: float | int | str, units: UnitSystem
) -> dict[str, Any]:
    return wrap_feature(
        "fillet",
        name,
        [
            query_param("entities", edge_ids),
            quantity_param("radius", units.length_expression(radius)),
        ],
    )


def chamfer_feature(
    *, name: str, edge_ids: list[str], distance: float | int | str, units: UnitSystem
) -> dict[str, Any]:
    return wrap_feature(
        "chamfer",
        name,
        [
            query_param("entities", edge_ids),
            enum_param("chamferType", "ChamferType", "EQUAL_OFFSETS"),
            quantity_param("width", units.length_expression(distance)),
        ],
    )


# --- variables -----------------------------------------------------------------------------

VARIABLE_KINDS = {"length": "LENGTH", "angle": "ANGLE", "number": "NUMBER"}
VARIABLE_VALUE_PARAMETER = {"LENGTH": "lengthValue", "ANGLE": "angleValue", "NUMBER": "numberValue"}


def variable_feature(name: str, kind: str, expression: str) -> dict[str, Any]:
    """A Part Studio variable (``assignVariable``), named after the variable.

    Checked live: the three parameters below are enough, and sketches / features can then use
    ``#name`` in any expression. The feature NAME must not start with ``#``: Onshape reads
    ``#word`` in a name as one of the feature's own parameters and shows ``?`` (the first
    version named it ``#d`` and the tree showed ``?``).
    """
    kind_id = VARIABLE_KINDS[kind]
    body = wrap_feature(
        "assignVariable",
        name,
        [
            enum_param("mode", "VariableMode", "ASSIGNED"),
            enum_param("variableType", "VariableType", kind_id),
            {"btType": "BTMParameterString-149", "value": name, "parameterId": "name"},
            quantity_param(VARIABLE_VALUE_PARAMETER[kind_id], expression),
        ],
    )
    return body


# --- responses ------------------------------------------------------------------------------


def describe_effect(
    before: dict[str, float], after: dict[str, float], units: UnitSystem
) -> dict[str, Any]:
    """What a feature changed in the Part Studio: part count and total volume, before and after.

    ``before`` / ``after`` map part ID -> volume in m^3. An accepted feature that left both
    unchanged did nothing (seen live with mates: ``OK`` and no effect), so that is flagged.
    """
    total_before, total_after = sum(before.values()), sum(after.values())
    same = set(before) == set(after) and math.isclose(
        total_before, total_after, rel_tol=1e-9, abs_tol=1e-15
    )
    out: dict[str, Any] = {
        "effect": {
            "parts_before": len(before),
            "parts_after": len(after),
            "volume_before": units.volume_from_m3(total_before),
            "volume_after": units.volume_from_m3(total_after),
            "volume_unit": units.volume_unit,
        }
    }
    if same:
        out["warning"] = (
            "Onshape accepted the feature but the Part Studio did not change (same parts, same "
            "total volume): it probably did nothing. Check the part, feature, face or axis IDs "
            "you passed, or screenshot the result."
        )
    return out


def summarize_feature_response(response: dict[str, Any]) -> dict[str, Any]:
    """Reduce an add/update-feature response to what a model needs: ID, name and health."""
    feature = response.get("feature") or {}
    state = response.get("featureState") or {}
    status = state.get("featureStatus", "UNKNOWN")
    out: dict[str, Any] = {
        "feature_id": feature.get("featureId"),
        "name": feature.get("name"),
        "feature_type": feature.get("featureType"),
        "status": status,
    }
    if status in {"ERROR", "WARNING"}:
        out["warning"] = (
            f"Onshape reports this feature as {status}. It was created but may not produce "
            f"geometry. Capture a screenshot or call list_features to inspect it, then fix it "
            f"with update_feature or remove it with delete_feature."
        )
    return out
