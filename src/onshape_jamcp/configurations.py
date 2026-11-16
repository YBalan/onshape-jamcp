"""Part Studio configurations: inputs such as a Size list (M10 / M18) or a Length.

Checked live: an enum input defined through ``POST .../configuration`` can drive a variable per
option (a *configured* variable value), and the whole model - sketches, extrudes, threads -
follows ``?configuration=Size=M18`` on any read call.

Inputs made in Onshape's own UI differ from the ones this server creates: the ID is generated
(``List_gfWXlOaT2Zmfyo``) and differs from the label you see (``Configuration``), and so do the
options (``_3Spoke`` vs ``3Spoke``). Calls to Onshape need the IDs; people and models use the
labels. :func:`find_input`, :func:`find_option` and :func:`resolve` accept either.
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


def input_label(parameter: dict[str, Any]) -> str:
    """The name shown in Onshape's UI (the ID when there is no separate name)."""
    return str(parameter.get("parameterName") or parameter.get("parameterId") or "")


def _lookup(wanted: str, pairs: list[tuple[str, Any]]) -> Any | None:
    """``pairs`` are (text, target): an exact match first, else a unique case-insensitive one."""
    for text, target in pairs:
        if text == wanted:
            return target
    folded = [target for text, target in pairs if text.casefold() == wanted.casefold()]
    return folded[0] if len(folded) == 1 else None


def find_input(
    parameters: list[dict[str, Any]], name: str, *, enum_only: bool = False
) -> dict[str, Any] | None:
    """The input called ``name`` - by its ID or by its label."""
    candidates = [p for p in parameters if not enum_only or is_enum(p)]
    pairs = [(str(p.get("parameterId")), p) for p in candidates]
    return _lookup(name, pairs + [(input_label(p), p) for p in candidates])


def find_option(parameter: dict[str, Any], wanted: object) -> dict[str, Any] | None:
    """The option of a list input called ``wanted`` - by its ID or by its label."""
    options = parameter.get("options") or []
    pairs = [(str(o.get("option")), o) for o in options]
    return _lookup(
        str(wanted), pairs + [(str(o.get("optionName") or o.get("option")), o) for o in options]
    )


def option_label(parameter: dict[str, Any] | None, option_id: object) -> str:
    option = next(
        (o for o in (parameter or {}).get("options") or [] if o.get("option") == option_id), None
    )
    return str((option or {}).get("optionName") or option_id)


def resolve(
    configuration: Configuration | None, parameters: list[dict[str, Any]] | None
) -> Configuration:
    """Translate labels to the IDs Onshape expects: ``{"Configuration": "3Spoke"}`` becomes
    ``{"List_gfWXlOaT2Zmfyo": "_3Spoke"}``. IDs pass through. Without a definition to check
    against, the entries are returned unchanged."""
    if not configuration or not parameters:
        return dict(configuration or {})
    resolved: dict[str, str | float | int] = {}
    for key, value in configuration.items():
        parameter = find_input(parameters, key)
        if parameter is None:
            known = ", ".join(repr(input_label(p)) for p in parameters)
            raise InputError(
                f"This element has no configuration input {key!r}. Its inputs: {known} "
                "(get_configuration lists them with their options)."
            )
        if not is_enum(parameter):
            resolved[parameter["parameterId"]] = value
            continue
        option = find_option(parameter, value)
        if option is None:
            choices = ", ".join(
                repr(option_label(parameter, o.get("option")))
                for o in parameter.get("options") or []
            )
            raise InputError(
                f"Configuration input {input_label(parameter)!r} has no option {value!r}; "
                f"its options are {choices}."
            )
        resolved[parameter["parameterId"]] = option["option"]
    return resolved


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
    labels = {
        o["option"]: o.get("optionName") or o["option"] for o in input_spec.get("options") or []
    }
    by_id: dict[str, str] = {}
    unknown: list[str] = []
    for key, expression in values.items():  # the caller may name options by label or by ID
        option = find_option(input_spec, key)
        if option is None:
            unknown.append(key)
        else:
            by_id[option["option"]] = expression
    missing = [label for option_id, label in labels.items() if option_id not in by_id]
    if unknown or missing:
        raise InputError(
            f"Give a value for every option of {input_name!r} ({list(labels.values())}); "
            f"unknown: {sorted(unknown)}, missing: {missing}."
        )
    input_id = input_spec.get("parameterId") or input_name
    return {
        "btType": "BTMParameterConfigured-2222",
        "parameterId": parameter_id,
        "configurationParameterId": input_id,
        "values": [
            {
                "btType": "BTMConfiguredValueByEnum-1923",
                "enumName": input_spec.get("enumName") or enum_name(input_id),
                "enumValue": option_id,
                "namespace": input_spec.get("namespace", ""),
                "value": {
                    "btType": "BTMParameterQuantity-147",
                    "isInteger": False,
                    "expression": by_id[option_id],
                },
            }
            for option_id in labels
        ],
    }
