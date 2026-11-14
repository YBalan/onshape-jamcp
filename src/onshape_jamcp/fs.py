"""FeatureScript helpers: decoding evaluation results and the scripts this server runs itself."""

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
