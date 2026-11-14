"""Named camera views for screenshots.

``viewMatrix`` for Onshape's shaded-views endpoint is either one of the named views the API knows
(top, bottom, front, back, left, right) or a 12-number 3x4 matrix. Isometric uses the matrix from
Onshape's own API documentation.
"""

from .errors import InputError

#: Taken verbatim from the "viewMatrix" description in Onshape's REST API reference.
_ISOMETRIC = "0.612,0.612,0,0,-0.354,0.354,0.707,0,0.707,-0.707,0.707,0"

NAMED_VIEWS: dict[str, str] = {
    "isometric": _ISOMETRIC,
    "front": "front",
    "back": "back",
    "left": "left",
    "right": "right",
    "top": "top",
    "bottom": "bottom",
}

MAX_VIEWS = 6


def resolve_view(name: str) -> str:
    key = name.strip().lower()
    try:
        return NAMED_VIEWS[key]
    except KeyError:
        raise InputError(f"Unknown view {name!r}. Use one of: {', '.join(NAMED_VIEWS)}.") from None
