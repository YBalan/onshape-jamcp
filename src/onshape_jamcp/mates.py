"""Assembly mates, in the structure Onshape itself produces.

Learned from a mate made by hand in the UI and replayed (checked live). My first attempts failed
silently - Onshape accepted them with status OK and moved nothing - because they put geometry
queries straight into the mate. A mate really is a ``BTMMate-64`` that **embeds two implicit mate
connectors** (``BTMMateConnector-66``, one per part, each an ``originQuery`` on a face / edge /
vertex of an instance) and refers to them by feature ID in ``mateConnectorsQuery``. The compressed
``entityQuery`` blob the UI also stores is not needed.
"""

import secrets
from typing import Any

from .errors import InputError
from .features import enum_param
from .units import UnitSystem

MATE_TYPES = {
    "fastened": "FASTENED",
    "revolute": "REVOLUTE",
    "slider": "SLIDER",
    "planar": "PLANAR",
    "cylindrical": "CYLINDRICAL",
    "pin_slot": "PIN_SLOT",
    "ball": "BALL",
    "parallel": "PARALLEL",
}
# Where on the picked entity the connector sits (Onshape's "inference types").
AT = {
    "center": "CENTER",  # of a circular edge or arc
    "centroid": "CENTROID",  # of a face
    "point": "POINT",  # a vertex
    "mid_point": "MID_POINT",  # of an edge
    "origin": "PART_ORIGIN",  # the part's origin; needs no entity
    "top_axis": "TOP_AXIS_POINT",  # end of a cylinder's axis
    "mid_axis": "MID_AXIS_POINT",
    "bottom_axis": "BOTTOM_AXIS_POINT",
}


def new_feature_id() -> str:
    return "M" + secrets.token_urlsafe(12)


def _empty_query(parameter_id: str) -> dict[str, Any]:
    return {
        "btType": "BTMParameterQueryWithOccurrenceList-67",
        "parameterId": parameter_id,
        "queries": [],
    }


def implicit_connector(
    feature_id: str, instance_id: str, at: str, entity_id: str | None
) -> dict[str, Any]:
    """A hidden mate connector on ``entity_id`` of the instance (or on its origin)."""
    if at not in AT:
        raise InputError(f"at must be one of {sorted(AT)}, got {at!r}.")
    if at != "origin" and not entity_id:
        raise InputError(f"at={at!r} needs an entity_id (a face / edge / vertex ID).")
    query: dict[str, Any] = {
        "btType": "BTMInferenceQueryWithOccurrence-1083",
        "inferenceType": AT[at],
        "deterministicIds": [entity_id] if entity_id and at != "origin" else [],
        "path": [instance_id],
    }
    return {
        "btType": "BTMMateConnector-66",
        "featureType": "mateConnector",
        "name": "Mate connector",
        "featureId": feature_id,
        "implicit": True,
        "isHidden": True,
        "suppressed": False,
        "namespace": "",
        "isAuxiliaryTreeMateConnector": False,
        "returnAfterSubfeatures": False,
        "parameters": [
            enum_param("originType", "Origin type", "ON_ENTITY"),
            {
                "btType": "BTMParameterQueryWithOccurrenceList-67",
                "parameterId": "originQuery",
                "queries": [query],
            },
            _empty_query("originAdditionalQuery"),
            _empty_query("primaryAxisQuery"),
            _empty_query("secondaryAxisQuery"),
            enum_param("rotationType", "Rotation axis", "ABOUT_Z"),
            enum_param("secondaryAxisType", "Reorient secondary axis", "PLUS_X"),
        ],
    }


def mate_feature(
    *,
    name: str,
    mate_type: str,
    sides: list[tuple[str, str, str | None]],
    flip: bool,
    offset: list[float | str] | None,
    units: UnitSystem,
) -> dict[str, Any]:
    """The ``BTFeatureDefinitionCall`` body for a mate; ``sides``: two (instance, at, entity)."""
    if mate_type not in MATE_TYPES:
        raise InputError(f"mate_type must be one of {sorted(MATE_TYPES)}, got {mate_type!r}.")
    if len(sides) != 2:
        raise InputError("A mate joins exactly two parts.")
    connectors = [
        implicit_connector(new_feature_id(), instance, at, entity) for instance, at, entity in sides
    ]
    parameters: list[dict[str, Any]] = [
        enum_param("mateType", "Mate type", MATE_TYPES[mate_type]),
        {
            "btType": "BTMParameterQueryWithOccurrenceList-67",
            "parameterId": "mateConnectorsQuery",
            "queries": [
                {
                    "btType": "BTMFeatureQueryWithOccurrence-157",
                    "queryData": "",
                    "path": [],
                    "featureId": c["featureId"],
                }
                for c in connectors
            ],
        },
    ]
    if flip:
        parameters.append(
            {
                "btType": "BTMParameterBoolean-144",
                "parameterId": "primaryAxisAlignment",
                "value": False,
            }
        )
    if offset:
        if len(offset) != 3:
            raise InputError("offset is [x, y, z] along the first connector's axes.")
        parameters.append(
            {"btType": "BTMParameterBoolean-144", "parameterId": "mateOffsetEnabled", "value": True}
        )
        for axis, value in zip("XYZ", offset, strict=True):
            parameters.append(
                {
                    "btType": "BTMParameterQuantity-147",
                    "isInteger": False,
                    "expression": units.length_expression(value),
                    "parameterId": f"mateOffsetDistance{axis}",
                }
            )
    return {
        "btType": "BTFeatureDefinitionCall-1406",
        "feature": {
            "btType": "BTMMate-64",
            "featureType": "mate",
            "name": name,
            "suppressed": False,
            "namespace": "",
            "mateConnectors": connectors,
            "parameters": parameters,
        },
    }
