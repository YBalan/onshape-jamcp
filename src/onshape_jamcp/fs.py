"""FeatureScript helpers: decoding evaluation results and the scripts this server runs itself."""

import re
from typing import Any

#: Onshape's built-in origin planes. Their internal IDs are *looked up* via FeatureScript rather
#: than hard-coded: a wrong constant would silently put sketches on the wrong plane (live Onshape
#: resolved Top to "JDC", which contradicted a remembered mapping).
STANDARD_PLANES = ("Top", "Front", "Right")


def unwrap(node: Any) -> Any:
    """Convert Onshape's typed ``BTFSValue*`` JSON into plain Python values.

    Two serializations exist and both are handled:

    * flat (what the live API returns today)::

        {"btType": "com.belmonttech.serialize.fsvalue.BTFSValueArray", "typeTag": "",
         "value": [{"btType": "...BTFSValueString", "typeTag": "", "value": "JDC"}]}

    * ``message``-wrapped (older clients): ``{"btType": "BTFSValueArray-1537",
      "message": {"value": [...]}}``.

    Arrays become lists, maps become dicts (keys stringified), scalars become their value.
    Anything that does not look like a typed value is returned unchanged.
    """
    if isinstance(node, list):
        return [unwrap(n) for n in node]
    if not isinstance(node, dict):
        return node
    bt_type = str(node.get("btType") or node.get("typeName") or "")
    message = node.get("message")
    if isinstance(message, dict):
        body = message
    elif "BTFSValue" in bt_type:
        body = node
    else:
        return node
    if "MapEntry" in bt_type:
        return (unwrap(body.get("key")), unwrap(body.get("value")))
    if "Map" in bt_type:
        out: dict[str, Any] = {}
        for entry in body.get("value") or []:
            key, value = unwrap(entry)
            out[str(key)] = value
        return out
    if "value" in body:
        return unwrap(body["value"])
    return {k: unwrap(v) for k, v in body.items() if k not in {"btType", "typeTag"}}


_IDENTIFIER = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def is_identifier(name: str) -> bool:
    return bool(_IDENTIFIER.match(name))


def variables_script(kinds: dict[str, str]) -> str:
    """FeatureScript returning ``{name: number}`` for Part Studio variables.

    ``kinds`` maps each variable to LENGTH (result in meters), ANGLE (degrees) or NUMBER.
    """
    unit = {"LENGTH": " / meter", "ANGLE": " / degree", "NUMBER": ""}
    lines = []
    for name, kind in kinds.items():
        if not is_identifier(name) or kind not in unit:  # also guards against script injection
            raise ValueError(f"Not a variable: {name!r} ({kind!r})")
        lines.append(f'out["{name}"] = getVariable(context, "{name}"){unit[kind]}; ')
    return (
        "function(context is Context, queries) { var out = {}; " + "".join(lines) + "return out; }"
    )


def tight_box_script(by_part: bool) -> str:
    """FeatureScript returning ``[minX, minY, minZ, maxX, maxY, maxZ]`` (meters) of Onshape's
    tight bounding box. The REST ``boundingboxes`` call is not tight for swept surfaces such as
    threads (seen live: 0.5 mm too large on each side)."""
    target = (
        "queries.part" if by_part else "qBodyType(qEverything(EntityType.BODY), BodyType.SOLID)"
    )
    return (
        "function(context is Context, queries) { "
        # NOTE: `box` is a reserved word in FeatureScript, hence `extent`.
        f'var extent = evBox3d(context, {{"topology" : {target}, "tight" : true}}); '
        "return [extent.minCorner[0] / meter, extent.minCorner[1] / meter, "
        "extent.minCorner[2] / meter, extent.maxCorner[0] / meter, "
        "extent.maxCorner[1] / meter, extent.maxCorner[2] / meter]; }"
    )


def plane_id_script(plane_name: str) -> str:
    """FeatureScript returning the transient ID of an origin plane (Top/Front/Right)."""
    if plane_name not in STANDARD_PLANES:  # also guards against script injection
        raise ValueError(f"Not a standard plane: {plane_name!r}")
    return (
        "function(context is Context, queries) { "
        "return transientQueriesToStrings(evaluateQuery(context, "
        f'qCreatedBy(makeId("{plane_name}"), EntityType.FACE))); '
        "}"
    )
