"""Adding a feature and checking that it did something."""

from typing import Annotated, Any

from pydantic import Field

from .. import features
from ..app import App
from ..client import q
from ..errors import OnshapeError

Verify = Annotated[
    bool,
    Field(
        description="Measure the Part Studio before and after (two extra requests) and report what "
        "changed: part count and total volume, plus a warning when nothing changed (Onshape can "
        "accept a feature that does nothing). Set false to save requests."
    ),
]


def features_path(document_id: str, workspace_id: str, element_id: str) -> str:
    return f"/partstudios/d/{q(document_id)}/w/{q(workspace_id)}/e/{q(element_id)}/features"


async def _snapshot(
    app: App, document_id: str, workspace_id: str, element_id: str
) -> dict[str, float] | None:
    try:
        return await app.volumes(document_id, workspace_id, element_id)
    except OnshapeError:
        return None  # the feature is still added; only the check is skipped


async def apply_feature(
    app: App,
    document_id: str,
    element_id: str,
    workspace_id: str | None,
    body: dict[str, Any],
    *,
    verify: bool = True,
    part_name: str | None = None,
) -> dict[str, Any]:
    """POST the feature and return its health, plus - when ``verify`` - what it changed.

    New parts are listed (and named ``part_name`` if given); parts that disappeared (a union)
    are listed too. A feature that left the part set and the total volume unchanged gets a
    warning, because Onshape reports ``OK`` for some features that do nothing.
    """
    wid = await app.workspace(document_id, workspace_id)
    before = await _snapshot(app, document_id, wid, element_id) if (verify or part_name) else None
    response = await app.client.post(features_path(document_id, wid, element_id), body)
    result = features.summarize_feature_response(response)
    if before is None:
        return result
    after = await _snapshot(app, document_id, wid, element_id)
    if after is None:
        result["note"] = (
            "Could not measure the Part Studio afterwards; check it with get_part_properties."
        )
        return result
    if verify:
        effect = features.describe_effect(before, after, app.units)
        result["effect"] = effect["effect"]
        if "warning" in effect and "warning" not in result:
            result["warning"] = effect["warning"]
    if set(after) - set(before):
        created, note = await app.name_new_parts(
            document_id, wid, element_id, set(before), part_name
        )
        result["parts_created"] = created
        if note:
            result["note"] = note
    if set(before) - set(after):
        result["parts_removed"] = sorted(set(before) - set(after))
    return result
