"""Sketch shapes (what the model sends) and their translation to Onshape `BTMSketch-151` JSON.

Coordinates are 2D points in the sketch plane, expressed in the configured length unit (mm by
default) or as strings with an explicit unit (`"1.5 in"`). Onshape stores sketch geometry in
meters, so everything is converted here.

Closed shapes are made of line/arc segments joined by COINCIDENT constraints so Onshape sees
a connected profile it can build regions from.

A size may be a Part Studio variable written ``"#name"``. The sketch then carries a dimension
constraint with that expression, so changing the variable (or a configuration that sets it)
reshapes the sketch. The initial geometry uses the variable's current value, which the caller
looks up and passes in ``variables`` (meters).
"""

import math
import re
from collections.abc import Mapping
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from .errors import InputError
from .units import UnitSystem

MAX_SHAPES = 200
_EPS = 1e-9

Length = Annotated[
    float | str,
    Field(
        description="Number in the configured length unit, a string like '12.5 mm' / '0.5 in', "
        "or a Part Studio variable such as '#diameter' (the sketch then follows the variable)."
    ),
]
_VARIABLE = re.compile(r"^#([A-Za-z_][A-Za-z0-9_]*)$")


def variable_name(value: object) -> str | None:
    """``"#d"`` -> ``"d"``; anything else -> ``None``."""
    match = _VARIABLE.match(value.strip()) if isinstance(value, str) else None
    return match.group(1) if match else None


def referenced_variables(shapes: list["Shape"]) -> set[str]:
    """Names of every ``#variable`` used as a size in ``shapes``."""
    found: set[str] = set()
    for shape in shapes:
        for field in ("radius", "diameter", "width", "height", "across_flats"):
            name = variable_name(getattr(shape, field, None))
            if name:
                found.add(name)
    return found


Point = Annotated[
    list[float | str],
    Field(
        min_length=2,
        max_length=2,
        description="[x, y] in the sketch plane, in the configured length unit "
        "(or strings with units, e.g. ['10 mm', '0.5 in']).",
    ),
]


class _Shape(BaseModel):
    construction: bool = Field(
        default=False,
        description="Construction geometry is a reference only: it never forms extrudable regions.",
    )


class Rectangle(_Shape):
    """Axis-aligned rectangle: `corner1` plus either the opposite corner `corner2`, or a `width`
    (along +X) and `height` (along +Y), which may be variables like '#width'."""

    type: Literal["rectangle"]
    corner1: Point
    corner2: Point | None = None
    width: Length | None = Field(default=None, description="With `height`, instead of corner2.")
    height: Length | None = Field(default=None, description="With `width`, instead of corner2.")


class Circle(_Shape):
    """Full circle. Give either `radius` or `diameter`."""

    type: Literal["circle"]
    center: Point
    radius: Length | None = Field(default=None, description="Radius (or give `diameter`).")
    diameter: Length | None = Field(default=None, description="Diameter (or give `radius`).")


class Line(_Shape):
    """Single line segment."""

    type: Literal["line"]
    start: Point
    end: Point


class Arc(_Shape):
    """Circular arc, counter-clockwise from `start_angle` to `end_angle` (degrees from +X)."""

    type: Literal["arc"]
    center: Point
    radius: Length
    start_angle: float = Field(
        description="Degrees, measured counter-clockwise from the sketch +X."
    )
    end_angle: float = Field(
        description="Degrees; the arc runs counter-clockwise up to this angle."
    )


class Polygon(_Shape):
    """Polyline through `points`; closed by default."""

    type: Literal["polygon"]
    points: Annotated[list[Point], Field(min_length=2)]
    closed: bool = True


class Hexagon(_Shape):
    """Regular hexagon with flat sides at the top and bottom (corners on the sketch X axis). Size
    it by `across_flats` (the wrench size, e.g. 16 for an M10 bolt head or '#head_af')."""

    type: Literal["hexagon"]
    center: Point
    across_flats: Length


Shape = Annotated[Rectangle | Circle | Line | Arc | Polygon | Hexagon, Field(discriminator="type")]


class _Builder:
    def __init__(self, units: UnitSystem, variables: Mapping[str, float] | None = None) -> None:
        self.units = units
        self.variables = variables or {}
        self.entities: list[dict[str, Any]] = []
        self.constraints: list[dict[str, Any]] = []
        self._counters: dict[str, int] = {}

    # --- ids / primitives ----------------------------------------------------------------------

    def _next_id(self, prefix: str) -> str:
        self._counters[prefix] = self._counters.get(prefix, 0) + 1
        return f"{prefix}{self._counters[prefix]}"

    def _pt(self, point: list[float | str], what: str) -> tuple[float, float]:
        try:
            return (
                self.units.length_to_meters(point[0]),
                self.units.length_to_meters(point[1]),
            )
        except InputError as exc:
            raise InputError(f"{what}: {exc}") from None

    def _len(self, value: float | str, what: str) -> float:
        try:
            return self.units.length_to_meters(value)
        except InputError as exc:
            raise InputError(f"{what}: {exc}") from None

    def _size(self, value: float | str, what: str) -> tuple[float, str | None]:
        """A size in meters and, for a ``#variable``, the expression that drives it."""
        name = variable_name(value)
        if name is None:
            return self._len(value, what), None
        if name not in self.variables:
            raise InputError(f"{what}: variable #{name} is not defined in this Part Studio.")
        return self.variables[name], f"#{name}"

    def _constrain(self, kind: str, constraint_id: str, *targets: str) -> None:
        names = ("localFirst", "localSecond")
        self.constraints.append(
            {
                "btType": "BTMSketchConstraint-2",
                "constraintType": kind,
                "entityId": constraint_id,
                "parameters": [
                    {"btType": "BTMParameterString-149", "value": t, "parameterId": names[i]}
                    for i, t in enumerate(targets)
                ],
            }
        )

    def _dimension(self, kind: str, constraint_id: str, target: str, expression: str) -> None:
        self._constrain(kind, constraint_id, target)
        self.constraints[-1]["parameters"].append(
            {
                "btType": "BTMParameterQuantity-147",
                "isInteger": False,
                "expression": expression,
                "parameterId": "length",
            }
        )

    def _coincident(self, first: str, second: str, constraint_id: str) -> None:
        self.constraints.append(
            {
                "btType": "BTMSketchConstraint-2",
                "constraintType": "COINCIDENT",
                "entityId": constraint_id,
                "parameters": [
                    {
                        "btType": "BTMParameterString-149",
                        "value": first,
                        "parameterId": "localFirst",
                    },
                    {
                        "btType": "BTMParameterString-149",
                        "value": second,
                        "parameterId": "localSecond",
                    },
                ],
            }
        )

    def _line(
        self, entity_id: str, p0: tuple[float, float], p1: tuple[float, float], construction: bool
    ) -> None:
        dx, dy = p1[0] - p0[0], p1[1] - p0[1]
        length = math.hypot(dx, dy)
        if length < _EPS:
            raise InputError(f"{entity_id}: zero-length line (both points are identical).")
        self.entities.append(
            {
                "btType": "BTMSketchCurveSegment-155",
                "entityId": entity_id,
                "startPointId": f"{entity_id}.start",
                "endPointId": f"{entity_id}.end",
                "startParam": 0.0,
                "endParam": length,
                "geometry": {
                    "btType": "BTCurveGeometryLine-117",
                    "pntX": p0[0],
                    "pntY": p0[1],
                    "dirX": dx / length,
                    "dirY": dy / length,
                },
                "isConstruction": construction,
            }
        )

    def _arc(
        self,
        entity_id: str,
        center: tuple[float, float],
        radius: float,
        start: float,
        end: float,
        construction: bool,
    ) -> None:
        self.entities.append(
            {
                "btType": "BTMSketchCurveSegment-155",
                "entityId": entity_id,
                "startPointId": f"{entity_id}.start",
                "endPointId": f"{entity_id}.end",
                "centerId": f"{entity_id}.center",
                "startParam": start,
                "endParam": end,
                "geometry": {
                    "btType": "BTCurveGeometryCircle-115",
                    "radius": radius,
                    "xCenter": center[0],
                    "yCenter": center[1],
                    "xDir": 1.0,
                    "yDir": 0.0,
                    "clockwise": False,
                },
                "isConstruction": construction,
            }
        )

    # --- shapes --------------------------------------------------------------------------------

    def add(self, shape: Shape, index: int) -> None:
        label = f"shapes[{index}] ({shape.type})"
        if isinstance(shape, Rectangle):
            self._rectangle(shape, label)
        elif isinstance(shape, Circle):
            self._circle(shape, label)
        elif isinstance(shape, Line):
            entity_id = self._next_id("line")
            self._line(
                entity_id,
                self._pt(shape.start, f"{label}.start"),
                self._pt(shape.end, f"{label}.end"),
                shape.construction,
            )
        elif isinstance(shape, Arc):
            self._arc_shape(shape, label)
        elif isinstance(shape, Polygon):
            points = [self._pt(p, f"{label}.points[{i}]") for i, p in enumerate(shape.points)]
            self._polyline(points, shape.closed, shape.construction, label)
        elif isinstance(shape, Hexagon):
            self._hexagon(shape, label)

    def _polyline(
        self, points: list[tuple[float, float]], closed: bool, construction: bool, label: str
    ) -> None:
        if closed and len(points) < 3:
            raise InputError(f"{label}: a closed polygon needs at least 3 points.")
        prefix = self._next_id("poly")
        count = len(points) if closed else len(points) - 1
        ids = [f"{prefix}.{i}" for i in range(count)]
        for i, entity_id in enumerate(ids):
            self._line(entity_id, points[i], points[(i + 1) % len(points)], construction)
        for i in range(count - 1):
            self._coincident(f"{ids[i]}.end", f"{ids[i + 1]}.start", f"{prefix}.join{i}")
        if closed:
            self._coincident(f"{ids[-1]}.end", f"{ids[0]}.start", f"{prefix}.join{count - 1}")

    def _rectangle(self, shape: Rectangle, label: str) -> None:
        x1, y1 = self._pt(shape.corner1, f"{label}.corner1")
        width_expr = height_expr = None
        if shape.corner2 is not None:
            if shape.width is not None or shape.height is not None:
                raise InputError(f"{label}: give corner2, or width and height - not both.")
            x2, y2 = self._pt(shape.corner2, f"{label}.corner2")
        else:
            if shape.width is None or shape.height is None:
                raise InputError(f"{label}: give corner2, or both width and height.")
            width, width_expr = self._size(shape.width, f"{label}.width")
            height, height_expr = self._size(shape.height, f"{label}.height")
            x2, y2 = x1 + width, y1 + height
        if abs(x2 - x1) < _EPS or abs(y2 - y1) < _EPS:
            raise InputError(f"{label}: rectangle has zero width or height.")
        self._polyline([(x1, y1), (x2, y1), (x2, y2), (x1, y2)], True, shape.construction, label)
        if width_expr or height_expr:
            ids = [f"poly{self._counters['poly']}.{i}" for i in range(4)]
            for i, line in enumerate(ids):  # keep it a rectangle when a size changes
                self._constrain("HORIZONTAL" if i % 2 == 0 else "VERTICAL", f"{line}.dir", line)
            self._constrain("FIX", f"{ids[0]}.anchor", f"{ids[0]}.start")
            if width_expr:
                self._dimension("LENGTH", f"{ids[0]}.width", ids[0], width_expr)
            if height_expr:
                self._dimension("LENGTH", f"{ids[1]}.height", ids[1], height_expr)

    def _hexagon(self, shape: Hexagon, label: str) -> None:
        flats, expression = self._size(shape.across_flats, f"{label}.across_flats")
        if flats <= _EPS:
            raise InputError(f"{label}: across_flats must be positive.")
        cx, cy = self._pt(shape.center, f"{label}.center")
        radius = flats / math.sqrt(3)  # corner radius = across flats / (2 cos 30 deg)
        corners = [
            (cx + radius * math.cos(math.radians(a)), cy + radius * math.sin(math.radians(a)))
            for a in range(0, 360, 60)
        ]
        self._polyline(corners, True, shape.construction, label)
        prefix = f"poly{self._counters['poly']}"
        # The corners lie on a construction circle, the sides are equal, the top side is
        # horizontal and the centre is fixed: a regular hexagon whose size is one dimension.
        circle = self._next_id("circle")
        upper, lower = f"{circle}.a", f"{circle}.b"
        self._arc(upper, (cx, cy), radius, 0.0, math.pi, True)
        self._arc(lower, (cx, cy), radius, math.pi, 2 * math.pi, True)
        self._coincident(f"{upper}.end", f"{lower}.start", f"{circle}.join0")
        self._coincident(f"{lower}.end", f"{upper}.start", f"{circle}.join1")
        self._coincident(f"{upper}.center", f"{lower}.center", f"{circle}.joincenter")
        for i in range(6):
            self._coincident(f"{prefix}.{i}.start", upper, f"{prefix}.oncircle{i}")
        for i in range(5):
            self._constrain("EQUAL", f"{prefix}.equal{i}", f"{prefix}.{i}", f"{prefix}.{i + 1}")
        self._constrain("HORIZONTAL", f"{prefix}.top", f"{prefix}.1")
        self._constrain("FIX", f"{prefix}.centre", f"{upper}.center")
        if expression:
            self._dimension("DIAMETER", f"{prefix}.size", upper, f"{expression} / cos(30 deg)")

    def _circle(self, shape: Circle, label: str) -> None:
        if (shape.radius is None) == (shape.diameter is None):
            raise InputError(f"{label}: give exactly one of 'radius' or 'diameter'.")
        dimension: tuple[str, str] | None = None  # (constraint type, expression)
        if shape.radius is not None:
            radius, expression = self._size(shape.radius, f"{label}.radius")
            dimension = ("RADIUS", expression) if expression else None
        else:
            assert shape.diameter is not None
            diameter, expression = self._size(shape.diameter, f"{label}.diameter")
            radius = diameter / 2
            dimension = ("DIAMETER", expression) if expression else None
        if radius <= _EPS:
            raise InputError(f"{label}: radius must be positive.")
        center = self._pt(shape.center, f"{label}.center")
        base = self._next_id("circle")
        upper, lower = f"{base}.a", f"{base}.b"
        # Two half-arcs closed by COINCIDENT constraints (the form Onshape round-trips reliably).
        self._arc(upper, center, radius, 0.0, math.pi, shape.construction)
        self._arc(lower, center, radius, math.pi, 2 * math.pi, shape.construction)
        self._coincident(f"{upper}.end", f"{lower}.start", f"{base}.join0")
        self._coincident(f"{lower}.end", f"{upper}.start", f"{base}.join1")
        self._coincident(f"{upper}.center", f"{lower}.center", f"{base}.joincenter")
        if dimension:
            self._dimension(dimension[0], f"{base}.size", upper, dimension[1])

    def _arc_shape(self, shape: Arc, label: str) -> None:
        radius = self._len(shape.radius, f"{label}.radius")
        if radius <= _EPS:
            raise InputError(f"{label}: radius must be positive.")
        start = math.radians(shape.start_angle)
        end = math.radians(shape.end_angle)
        while end <= start + _EPS:
            end += 2 * math.pi
        if end - start >= 2 * math.pi - 1e-6:
            raise InputError(f"{label}: arc sweeps a full circle - use a 'circle' shape instead.")
        self._arc(
            self._next_id("arc"),
            self._pt(shape.center, f"{label}.center"),
            radius,
            start,
            end,
            shape.construction,
        )


def build_sketch_feature(
    name: str,
    plane_id: str,
    shapes: list[Shape],
    units: UnitSystem,
    variables: Mapping[str, float] | None = None,
) -> dict[str, Any]:
    """Build the `BTFeatureDefinitionCall` body that creates a sketch on `plane_id`."""
    if not shapes:
        raise InputError("Provide at least one shape.")
    if len(shapes) > MAX_SHAPES:
        raise InputError(f"At most {MAX_SHAPES} shapes per sketch; got {len(shapes)}.")
    builder = _Builder(units, variables)
    for index, shape in enumerate(shapes):
        builder.add(shape, index)
    return {
        "btType": "BTFeatureDefinitionCall-1406",
        "feature": {
            "btType": "BTMSketch-151",
            "featureType": "newSketch",
            "name": name,
            "suppressed": False,
            "parameters": [
                {
                    "btType": "BTMParameterQueryList-148",
                    "queries": [
                        {"btType": "BTMIndividualQuery-138", "deterministicIds": [plane_id]}
                    ],
                    "parameterId": "sketchPlane",
                }
            ],
            "entities": builder.entities,
            "constraints": builder.constraints,
        },
    }
