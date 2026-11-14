"""Sketch shapes (what the model sends) and their translation to Onshape `BTMSketch-151` JSON.

Coordinates are 2D points in the sketch plane, expressed in the configured length unit (mm by
default) or as strings with an explicit unit (`"1.5 in"`). Onshape stores sketch geometry in
meters, so everything is converted here.

Closed shapes are made of line/arc segments joined by COINCIDENT constraints so Onshape sees
a connected profile it can build regions from.
"""

import math
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

from .errors import InputError
from .units import UnitSystem

MAX_SHAPES = 200
_EPS = 1e-9

Length = Annotated[
    float | str,
    Field(
        description="Number in the configured length unit, or a string like '12.5 mm' / '0.5 in'."
    ),
]
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
    """Axis-aligned rectangle given by two opposite corners."""

    type: Literal["rectangle"]
    corner1: Point
    corner2: Point


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


Shape = Annotated[Rectangle | Circle | Line | Arc | Polygon, Field(discriminator="type")]


class _Builder:
    def __init__(self, units: UnitSystem) -> None:
        self.units = units
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
        x2, y2 = self._pt(shape.corner2, f"{label}.corner2")
        if abs(x2 - x1) < _EPS or abs(y2 - y1) < _EPS:
            raise InputError(f"{label}: rectangle has zero width or height.")
        self._polyline([(x1, y1), (x2, y1), (x2, y2), (x1, y2)], True, shape.construction, label)

    def _circle(self, shape: Circle, label: str) -> None:
        if (shape.radius is None) == (shape.diameter is None):
            raise InputError(f"{label}: give exactly one of 'radius' or 'diameter'.")
        if shape.radius is not None:
            radius = self._len(shape.radius, f"{label}.radius")
        else:
            assert shape.diameter is not None
            radius = self._len(shape.diameter, f"{label}.diameter") / 2
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
    name: str, plane_id: str, shapes: list[Shape], units: UnitSystem
) -> dict[str, Any]:
    """Build the `BTFeatureDefinitionCall` body that creates a sketch on `plane_id`."""
    if not shapes:
        raise InputError("Provide at least one shape.")
    if len(shapes) > MAX_SHAPES:
        raise InputError(f"At most {MAX_SHAPES} shapes per sketch; got {len(shapes)}.")
    builder = _Builder(units)
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
