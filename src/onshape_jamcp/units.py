"""Unit systems.

Onshape's REST API speaks SI base units (meters, kilograms, radians for sketch arcs), while people
and LLMs speak millimeters - or inches. :class:`UnitSystem` converts at the edge so that every tool
argument and every tool result uses the *configured* length unit (metric/mm by default).

Angles are always degrees.
"""

import math
import re
from dataclasses import dataclass

from .errors import InputError

_METERS_PER = {"mm": 0.001, "cm": 0.01, "m": 1.0, "in": 0.0254, "ft": 0.3048, "yd": 0.9144}

_NUMBER = r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?"
_QUANTITY_RE = re.compile(rf"^\s*({_NUMBER})\s*([a-zA-Z]*)\s*$")
# Onshape expressions: variable references (#name), arithmetic, units.
_EXPRESSION_RE = re.compile(r"^[\w#\s.+\-*/()^,]+$")


def _num(value: float) -> str:
    return format(float(value), ".12g")


@dataclass(frozen=True)
class UnitSystem:
    """A named length system (``metric`` -> mm, ``imperial`` -> in)."""

    name: str
    length_unit: str
    decimals: int

    # --- constants -----------------------------------------------------------------------------

    @property
    def meters_per_unit(self) -> float:
        return _METERS_PER[self.length_unit]

    @property
    def area_unit(self) -> str:
        return f"{self.length_unit}^2"

    @property
    def volume_unit(self) -> str:
        return f"{self.length_unit}^3"

    # --- outputs: API (SI) -> configured unit ---------------------------------------------------

    def from_meters(self, meters: float) -> float:
        return round(meters / self.meters_per_unit, self.decimals)

    def area_from_m2(self, square_meters: float) -> float:
        return round(square_meters / self.meters_per_unit**2, self.decimals)

    def volume_from_m3(self, cubic_meters: float) -> float:
        return round(cubic_meters / self.meters_per_unit**3, self.decimals)

    def point_from_meters(self, point: list[float] | tuple[float, ...]) -> list[float]:
        return [self.from_meters(c) for c in point]

    # --- inputs: configured unit -> API (SI) ----------------------------------------------------

    def to_meters(self, value: float) -> float:
        return float(value) * self.meters_per_unit

    def length_to_meters(self, value: float | int | str) -> float:
        """Convert a plain number (configured unit) or a ``"<number> <unit>"`` string to meters.

        Used for sketch geometry, which the API takes as raw meters and cannot evaluate
        expressions for.
        """
        if isinstance(value, bool):
            raise InputError(f"Expected a length, got boolean {value!r}.")
        if isinstance(value, int | float):
            if not math.isfinite(value):
                raise InputError(f"Length must be finite, got {value!r}.")
            return self.to_meters(value)
        if isinstance(value, str):
            m = _QUANTITY_RE.match(value)
            if m:
                number, unit = float(m.group(1)), m.group(2).lower()
                if not unit:
                    return self.to_meters(number)
                if unit in _METERS_PER:
                    return number * _METERS_PER[unit]
                raise InputError(
                    f"Unknown length unit {unit!r} in {value!r}. "
                    f"Use one of: {', '.join(_METERS_PER)}."
                )
        raise InputError(
            f"Cannot read {value!r} as a length. Use a number in {self.length_unit} "
            f"or a string like '12.5 mm' / '0.5 in' (variables are not allowed in sketch geometry)."
        )

    def length_expression(self, value: float | int | str) -> str:
        """Build an Onshape expression string such as ``"10 mm"``.

        Numbers are interpreted in the configured unit. Strings may carry an explicit unit
        (``"2 in"``) or be a full Onshape expression (``"#width"``, ``"#width / 2"``).
        """
        if isinstance(value, bool):
            raise InputError(f"Expected a length, got boolean {value!r}.")
        if isinstance(value, int | float):
            if not math.isfinite(value):
                raise InputError(f"Length must be finite, got {value!r}.")
            return f"{_num(value)} {self.length_unit}"
        if isinstance(value, str):
            text = value.strip()
            if not text:
                raise InputError("Length expression is empty.")
            m = _QUANTITY_RE.match(text)
            if m:
                number, unit = m.group(1), m.group(2).lower()
                if not unit:
                    return f"{_num(float(number))} {self.length_unit}"
                if unit in _METERS_PER:
                    return f"{_num(float(number))} {unit}"
            if _EXPRESSION_RE.match(text):
                return text
        raise InputError(
            f"Cannot read {value!r} as a length. Use a number in {self.length_unit}, "
            f"a string with a unit like '12.5 mm', or an Onshape expression like '#width'."
        )

    @staticmethod
    def angle_expression(degrees: float | int | str) -> str:
        if isinstance(degrees, bool):
            raise InputError(f"Expected an angle, got boolean {degrees!r}.")
        if isinstance(degrees, int | float):
            if not math.isfinite(degrees):
                raise InputError(f"Angle must be finite, got {degrees!r}.")
            return f"{_num(degrees)} deg"
        if isinstance(degrees, str):
            text = degrees.strip()
            m = _QUANTITY_RE.match(text)
            if m and not m.group(2):
                return f"{_num(float(m.group(1)))} deg"
            if text and _EXPRESSION_RE.match(text):
                return text
        raise InputError(f"Cannot read {degrees!r} as an angle (degrees).")


METRIC = UnitSystem(name="metric", length_unit="mm", decimals=4)
IMPERIAL = UnitSystem(name="imperial", length_unit="in", decimals=5)

_ALIASES = {
    "metric": METRIC,
    "mm": METRIC,
    "millimeter": METRIC,
    "millimeters": METRIC,
    "si": METRIC,
    "imperial": IMPERIAL,
    "inch": IMPERIAL,
    "inches": IMPERIAL,
    "in": IMPERIAL,
    "us": IMPERIAL,
}


def parse_unit_system(value: str) -> UnitSystem:
    """Parse a user-supplied setting such as ``metric``, ``mm``, ``imperial`` or ``inches``."""
    try:
        return _ALIASES[value.strip().lower()]
    except (KeyError, AttributeError):
        raise InputError(
            f"Unknown unit system {value!r}. Use 'metric' (millimeters, default) or "
            f"'imperial' (inches)."
        ) from None
