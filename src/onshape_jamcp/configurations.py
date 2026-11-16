"""Part Studio configurations: inputs such as a Size list (M10 / M18) or a Length.

Checked live: an enum input defined through ``POST .../configuration`` can drive a variable per
option (a *configured* variable value), and the whole model - sketches, extrudes, threads -
follows ``?configuration=Size=M18`` on any read call.
"""

from typing import Any

from .errors import InputError
from .units import UnitSystem

Configuration = dict[str, str | float | int]

_QUANTITY_UNITS = {
    "length": ("LENGTH", "meter"),
    "angle": ("ANGLE", "radian"),
    "number": ("REAL", ""),
}


def encode(configuration: Configuration | None, units: UnitSystem) -> str | None:
    """Query-string value for ``configuration``: ``{"Size": "M18"}`` -> ``Size=M18``.

    Numbers are lengths in the configured unit (written with the unit).
    """
    if not configuration:
        return None
    parts = []
    for key, value in configuration.items():
        if ";" in key or "=" in key or ";" in str(value):
            raise InputError(f"Configuration entry {key!r}={value!r} contains ';' or '='.")
        text = (
            units.length_expression(value)
            if isinstance(value, int | float) and not isinstance(value, bool)
            else str(value)
        )
        parts.append(f"{key}={text}")
    return ";".join(parts)


def is_enum(parameter: dict[str, Any]) -> bool:
    """A list input. Identified by ``btType``: the live service omits ``parameterType`` when it
    reads inputs back."""
    return str(parameter.get("btType", "")).startswith("BTMConfigurationParameterEnum")


def enum_name(input_name: str) -> str:
    return f"{input_name}_conf"


def enum_input(name: str, options: list[str], default: str) -> dict[str, Any]:
    if len(set(options)) != len(options) or len(options) < 2:
        raise InputError("A list input needs at least two distinct options.")
    if default not in options:
        raise InputError(f"default {default!r} is not one of the options {options}.")
    return {
        "btType": "BTMConfigurationParameterEnum-105",
        "parameterId": name,
        "parameterName": name,
        "parameterType": "ENUM",
        "enumName": enum_name(name),
        "namespace": "",
        "defaultValue": default,
        "options": [{"btType": "BTMEnumOption-592", "option": o, "optionName": o} for o in options],
    }


def quantity_input(
    name: str,
    kind: str,
    default: float,
    minimum: float,
    maximum: float,
    units: UnitSystem,
) -> dict[str, Any]:
    """A numeric input, usable in any expression as ``#name`` (lengths: configured unit)."""
    quantity_type, unit = _QUANTITY_UNITS[kind]
    to_si = units.to_meters if kind == "length" else float
    return {
        "btType": "BTMConfigurationParameterQuantity-1826",
        "parameterId": name,
        "parameterName": name,
        "parameterType": "QUANTITY",
        "quantityType": quantity_type,
        "rangeAndDefault": {
            "btType": "BTQuantityRange-181",
            "minValue": to_si(minimum),
            "maxValue": to_si(maximum),
            "defaultValue": to_si(default),
            "units": unit,
        },
    }


def current_for(parameters: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """``currentConfiguration`` selecting every enum input's default."""
    return [
        {
            "btType": "BTMParameterEnum-145",
            "parameterId": p["parameterId"],
            "enumName": p.get("enumName"),
            "namespace": p.get("namespace", ""),
            "value": p.get("defaultValue"),
        }
        for p in parameters
        if is_enum(p)
    ]


def configured_parameter(
    parameter_id: str,
    input_name: str,
    input_spec: dict[str, Any],
    values: dict[str, str],
) -> dict[str, Any]:
    """A quantity parameter whose value depends on the option of a list input.

    ``values`` maps every option to its expression. Onshape rejects inner values that carry
    their own ``parameterId`` (seen live), so they have none.
    """
    options = [o["option"] for o in input_spec.get("options") or []]
    unknown = sorted(set(values) - set(options))
    missing = [o for o in options if o not in values]
    if unknown or missing:
        raise InputError(
            f"Give a value for every option of {input_name!r} ({options}); "
            f"unknown: {unknown}, missing: {missing}."
        )
    return {
        "btType": "BTMParameterConfigured-2222",
        "parameterId": parameter_id,
        "configurationParameterId": input_name,
        "values": [
            {
                "btType": "BTMConfiguredValueByEnum-1923",
                "enumName": input_spec.get("enumName") or enum_name(input_name),
                "enumValue": option,
                "namespace": input_spec.get("namespace", ""),
                "value": {
                    "btType": "BTMParameterQuantity-147",
                    "isInteger": False,
                    "expression": values[option],
                },
            }
            for option in options
        ],
    }
