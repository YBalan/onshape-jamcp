"""Names of parts and tabs: Onshape's property ID for them and the default names it hands out.

A part or tab that still has its default name ("Part 2", "Part Studio 1") tells nobody what it is,
so tools flag those and the assistant is told to rename them (see ``server.INSTRUCTIONS``).
"""

import re

# Onshape's built-in "Name" metadata property: the same ID for parts and for elements (tabs).
NAME_PROPERTY_ID = "57f3fb8efa3416c06701d60d"

_DEFAULT_TAB = re.compile(
    r"^(Part Studio|Assembly|Drawing|Feature Studio|Variable Studio)\s+\d+$", re.IGNORECASE
)
_DEFAULT_PART = re.compile(r"^Part\s+\d+$", re.IGNORECASE)
# "Extrude 8 mm", "Cut 0.5 in", "Extrude #head_h (symmetric)": names this server generates.
_AUTO_EXTRUDE = re.compile(
    r"^(Extrude|Cut) (?:[-+]?\d[\d.]*\s?[A-Za-z]+|#[A-Za-z_]\w*)(?: \(symmetric\))?$"
)


def is_default_tab_name(name: object) -> bool:
    return bool(_DEFAULT_TAB.match(str(name or "").strip()))


def is_default_part_name(name: object) -> bool:
    return bool(_DEFAULT_PART.match(str(name or "").strip()))


def extrude_name(operation: str, depth: str, *, symmetric: bool = False, hole: bool = False) -> str:
    """Default name of an extrude: what it does and how big. Holes get no size ("Hole")."""
    if hole:
        return "Hole"
    verb = "Cut" if operation == "remove" else "Extrude"
    return f"{verb} {depth}{' (symmetric)' if symmetric else ''}"[:100]


def _short(text: str) -> str:
    return text[:100]


def circular_pattern_name(count: str, angle: str) -> str:
    """ "Circular pattern x6" (a variable shows as "x#spokes"); a partial angle is appended."""
    suffix = "" if angle.replace(" deg", "") == "360" else f" over {angle}"
    return _short(f"Circular pattern x{count}{suffix}")


def linear_pattern_name(count: str, distance: str, second: tuple[str, str] | None = None) -> str:
    text = f"Linear pattern x{count} @ {distance}"
    if second:
        text += f", x{second[0]} @ {second[1]}"
    return _short(text)


def mirror_name(plane: str) -> str:
    return _short(f"Mirror ({plane})")


def revolve_name(angle: str) -> str:
    return _short(f"Revolve {angle}")


def shell_name(thickness: str) -> str:
    return _short(f"Shell {thickness}")


def boolean_name(operation: str) -> str:
    return {"union": "Union", "subtract": "Subtract", "intersect": "Intersect"}.get(
        operation, "Boolean"
    )


def is_auto_extrude_name(name: object) -> bool:
    """True for a name made by ``extrude_name`` (so it may be refreshed when the depth changes)."""
    return bool(_AUTO_EXTRUDE.match(str(name or "")))


def name_update(name: str) -> dict:
    """Request body for the metadata API that sets the Name property."""
    return {"properties": [{"propertyId": NAME_PROPERTY_ID, "value": name}]}
