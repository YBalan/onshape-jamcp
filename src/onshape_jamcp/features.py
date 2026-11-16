"""Builders for Part Studio features (extrude, fillet, chamfer) and response helpers."""

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


def quantity_param(parameter_id: str, expression: str) -> dict[str, Any]:
    return {
        "btType": "BTMParameterQuantity-147",
        "isInteger": False,
        "expression": expression,
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
    """A Part Studio variable (``assignVariable``), shown in Onshape as ``#name``.

    Checked live: the three parameters below are enough, and sketches / features can then use
    ``#name`` in any expression.
    """
    kind_id = VARIABLE_KINDS[kind]
    body = wrap_feature(
        "assignVariable",
        f"#{name}",
        [
            enum_param("mode", "VariableMode", "ASSIGNED"),
            enum_param("variableType", "VariableType", kind_id),
            {"btType": "BTMParameterString-149", "value": name, "parameterId": "name"},
            quantity_param(VARIABLE_VALUE_PARAMETER[kind_id], expression),
        ],
    )
    return body


# --- responses ------------------------------------------------------------------------------


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
