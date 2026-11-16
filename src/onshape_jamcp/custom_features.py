"""Custom features: scripts written in a Feature Studio (Thread Creator, ...) as opposed to the
features Onshape ships with.

Onshape describes every feature, built in or custom, with a *feature spec*
(``GET .../featurespecs``): the feature type, where its definition lives (``namespace``, empty for
built-ins) and each parameter with its type and default value. Nothing here is guessed: the
namespace is passed through exactly as Onshape reports it, and a new feature is built from the
spec's own default parameter values with the caller's values laid on top - which is what Onshape's
own UI does when you click a feature in the toolbar.
"""

import copy
import math
import re
from typing import Any

from .errors import InputError
from .features import bool_param, quantity_param, query_param
from .units import UnitSystem

ParameterValue = float | int | str | bool | list[str]

# Spec type (btType without its "-NNN" suffix) -> how a caller can set it.
_KINDS = {
    "BTParameterSpecQuantity": "quantity",
    "BTParameterSpecEnum": "enum",
    "BTParameterSpecMultiEnum": "multi_enum",
    "BTParameterSpecBoolean": "boolean",
    "BTParameterSpecString": "string",
    "BTParameterSpecQuery": "query",
}
# Dialog furniture with no value to set; hidden from listings instead of reported as unsupported.
_UI_ONLY = {
    "BTParameterSpecNotice",
    "BTParameterSpecButton",
    "BTParameterSpecProgress",
    "BTParameterSpecIcon",
}
# Quantity types for which a bare number is unitless (anything else needs an explicit unit).
_UNITLESS = {None, "", "UNKNOWN", "INTEGER", "REAL", "ANYTHING"}

# What Onshape reports for a Feature Studio's own features: element + element microversion. From
# another document a feature must be addressed as d<doc>::v<version>::e<element>::m<microversion>
# (verified live: Onshape resolves it to the same spec).
_LOCAL_NAMESPACE = re.compile(r"^e(?P<eid>[0-9a-f]{24})::m(?P<mid>[0-9a-f]{24})$", re.IGNORECASE)

_STUDIO_URL = re.compile(
    r"documents/(?P<did>[0-9a-f]{24})/(?P<wvm>[wvm])/(?P<wvmid>[0-9a-f]{24})/e/(?P<eid>[0-9a-f]{24})",
    re.IGNORECASE,
)


def _kind_name(node: dict[str, Any]) -> str:
    return str(node.get("btType", "")).split("-")[0]


def is_custom(item: dict[str, Any]) -> bool:
    """True for a feature spec or feature instance defined by a Feature Studio script.

    Features of Onshape's standard library have an empty ``namespace``.
    """
    return bool(str(item.get("namespace") or "").strip())


def parse_feature_studio_url(url: str) -> tuple[str, str, str, str]:
    """Return ``(document_id, wvm, wvm_id, element_id)`` from an Onshape Feature Studio URL.

    Only the IDs are extracted; the URL is never requested, so the host does not matter.
    """
    match = _STUDIO_URL.search(url or "")
    if not match:
        raise InputError(
            "feature_studio_url must be the browser URL of a Feature Studio tab, like "
            "https://cad.onshape.com/documents/<did>/w/<wid>/e/<eid> (/v/<version> and "
            "/m/<microversion> work too)."
        )
    return match["did"], match["wvm"].lower(), match["wvmid"], match["eid"]


ERROR_HINT = (
    "Onshape does not say why a custom feature fails (the API only reports the status). Check "
    "the selection and the values against list_custom_features. To see what Onshape itself "
    "produces, create one instance by hand in Onshape, read it with list_features(feature_id=...) "
    "and compare it with this one or replay it with add_feature."
)


def qualify_namespace(spec: dict[str, Any], studio: tuple[str, str, str, str]) -> dict[str, Any]:
    """Make a spec read from another document's Feature Studio usable from this document.

    ``studio`` is the parsed Feature Studio URL, which must be a *version* URL: Onshape links
    custom features to a version of the document that defines them. Specs whose namespace is
    already qualified (or in a form we do not know) are returned untouched.
    """
    document_id, _, version_id, _ = studio
    old = str(spec.get("namespace") or "")
    local = _LOCAL_NAMESPACE.match(old)
    if not local:
        return spec
    new = f"d{document_id}::v{version_id}::e{local['eid']}::m{local['mid']}"
    return _replace_text(spec, old, new)  # enum parameters repeat the namespace; keep them equal


def _replace_text(node: Any, old: str, new: str) -> Any:
    """A copy of ``node`` with every string equal to ``old`` replaced by ``new``."""
    if isinstance(node, str):
        return new if node == old else node
    if isinstance(node, list):
        return [_replace_text(item, old, new) for item in node]
    if isinstance(node, dict):
        return {key: _replace_text(value, old, new) for key, value in node.items()}
    return node


def parameter_specs(spec: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """The spec's parameters by ID, in declaration order."""
    found: dict[str, dict[str, Any]] = {}
    for source in (
        spec.get("parameters"),
        spec.get("allParameters"),
        (spec.get("parameterIdToSpec") or {}).values(),
    ):
        for parameter in source or []:
            pid = parameter.get("parameterId") if isinstance(parameter, dict) else None
            if isinstance(pid, str) and pid and pid not in found:
                found[pid] = parameter
    return found


# --- describing a spec to the model -----------------------------------------------------------


def _quantity_default(default: dict[str, Any], units: UnitSystem) -> Any:
    """Readable default of a quantity. Real specs often leave ``expression`` empty and carry the
    number in ``value`` (SI: meters / radians) instead."""
    expression = str(default.get("expression") or "").strip()
    if expression:
        return expression
    value = default.get("value")
    if not isinstance(value, int | float) or isinstance(value, bool):
        return None
    unit = str(default.get("units") or "").strip().lower()  # per default: meter, degree, radian
    if unit in ("meter", "m"):
        return f"{units.from_meters(value):g} {units.length_unit}"
    if unit in ("degree", "deg"):
        return f"{value:.10g} deg"
    if unit in ("radian", "rad"):
        return f"{math.degrees(value):.10g} deg"
    return f"{value:.12g} {unit}".strip()


def _default_of(kind: str, parameter: dict[str, Any], units: UnitSystem) -> Any:
    default = parameter.get("defaultValue")
    if not isinstance(default, dict):
        return None
    if kind == "quantity":
        return _quantity_default(default, units)
    if kind == "multi_enum":
        return default.get("values")
    if kind == "query":
        return None
    return default.get("value")


def _describe_parameter(
    pid: str, kind: str, parameter: dict[str, Any], units: UnitSystem
) -> dict[str, Any]:
    item: dict[str, Any] = {
        "id": pid,
        "name": parameter.get("parameterName") or parameter.get("localizedName") or pid,
        "type": kind,
    }
    if kind == "quantity":
        quantity = parameter.get("quantityType")
        unit = {"LENGTH": units.length_unit, "ANGLE": "deg"}.get(quantity)
        if unit:
            item["unit"] = unit
        elif quantity == "INTEGER":
            item["type"] = "integer"
    default = _default_of(kind, parameter, units)
    if default not in (None, "", []):
        item["default"] = default
    if kind in ("enum", "multi_enum"):
        options = list(parameter.get("options") or parameter.get("enumOptions") or [])
        item["options"] = options
        labels = list(parameter.get("optionNames") or [])
        if len(labels) == len(options) and labels != options:
            item["option_labels"] = dict(zip(options, labels, strict=True))
    if kind == "query":
        item["hint"] = "list of face / edge / part IDs (from get_body_details or list_parts)"
    if parameter.get("parameterDescription"):
        item["description"] = str(parameter["parameterDescription"])[:200]
    return item


def summarize_spec(spec: dict[str, Any], units: UnitSystem) -> dict[str, Any]:
    """Reduce a feature spec to what a model needs to call the feature."""
    out: dict[str, Any] = {
        "feature_type": spec.get("featureType"),
        "name": spec.get("featureTypeName") or spec.get("localizedName") or spec.get("featureType"),
        "namespace": spec.get("namespace"),
    }
    if spec.get("featureTypeDescription"):
        out["description"] = str(spec["featureTypeDescription"])[:300]
    settable: list[dict[str, Any]] = []
    unsupported: list[dict[str, str]] = []
    for pid, parameter in parameter_specs(spec).items():
        kind = _KINDS.get(_kind_name(parameter))
        if kind:
            settable.append(_describe_parameter(pid, kind, parameter, units))
        elif _kind_name(parameter) not in _UI_ONLY:
            label = _kind_name(parameter).removeprefix("BTParameterSpec") or "unknown"
            unsupported.append({"id": pid, "type": label})
    out["parameters"] = settable
    if unsupported:
        out["unsupported_parameters"] = unsupported
    return out


# --- choosing a spec ----------------------------------------------------------------------------


def pick_spec(
    specs: list[dict[str, Any]], feature_type: str, namespace: str | None = None
) -> dict[str, Any]:
    """Find the custom feature ``feature_type`` (in ``namespace`` when given), or explain what
    exists. Built-in features are never matched."""
    matches = [
        s
        for s in specs
        if is_custom(s)
        and s.get("featureType") == feature_type
        and (namespace is None or s.get("namespace") == namespace)
    ]
    if not matches:
        available = sorted({str(s.get("featureType")) for s in specs if is_custom(s)})
        raise InputError(
            f"No custom feature of type {feature_type!r} is available here. "
            + (
                f"Available custom feature types: {available}."
                if available
                else "No custom features are available to this Part Studio."
            )
            + " Call list_custom_features (it explains how to make an account feature "
            "available), pass feature_studio_url, or copy a feature that already exists with "
            "list_features(feature_id=...) and add_feature."
        )
    namespaces = sorted({str(s.get("namespace") or "") for s in matches})
    if len(namespaces) > 1:
        raise InputError(
            f"{len(namespaces)} different custom features share the type {feature_type!r} "
            f"(namespaces: {namespaces}). Pass `namespace` to choose one."
        )
    return matches[0]


# --- building a feature ------------------------------------------------------------------------


def _quantity_expression(pid: str, quantity: str | None, value: Any, units: UnitSystem) -> str:
    if isinstance(value, list):
        raise InputError(f"{pid}: expected a number or expression, got a list.")
    if quantity == "LENGTH":
        return units.length_expression(value)
    if quantity == "ANGLE":
        return units.angle_expression(value)
    if isinstance(value, int | float) and not isinstance(value, bool) and quantity not in _UNITLESS:
        raise InputError(
            f"{pid}: this is a {str(quantity).lower()} quantity, so a bare number is ambiguous. "
            f"Pass a string with a unit, for example '2 kg'."
        )
    return units.number_expression(value)


def _fresh_default(pid: str, parameter: dict[str, Any]) -> dict[str, Any]:
    """A copy of the spec's default value for ``pid`` (empty when the spec has none).

    ``nodeId`` is dropped: it identifies a node of the spec, not of the new feature.
    """
    default = parameter.get("defaultValue")
    copied: dict[str, Any] = copy.deepcopy(default) if isinstance(default, dict) else {}
    copied.pop("nodeId", None)
    if copied:
        copied["parameterId"] = pid
    return copied


def _set_value(
    pid: str, kind: str, parameter: dict[str, Any], value: Any, units: UnitSystem
) -> dict[str, Any]:
    """Return the BTM parameter for ``pid`` carrying ``value``, based on Onshape's own default."""
    base = _fresh_default(pid, parameter)
    base["parameterId"] = pid
    base.pop("valueString", None)  # the text form of the old value would contradict the new one

    if kind == "quantity":
        expression = _quantity_expression(pid, parameter.get("quantityType"), value, units)
        param = {**quantity_param(pid, expression), **base, "expression": expression}
        param.pop("value", None)
        if parameter.get("quantityType") == "INTEGER":
            param["isInteger"] = True
        return param
    if kind == "boolean":
        if not isinstance(value, bool):
            raise InputError(f"{pid}: expected true or false.")
        return {**bool_param(pid, value), **base, "value": value}
    if kind == "string":
        if not isinstance(value, str):
            raise InputError(f"{pid}: expected a string.")
        return {"btType": "BTMParameterString-149", **base, "value": value}
    if kind == "query":
        ids = [value] if isinstance(value, str) else value
        if not isinstance(ids, list) or not ids or not all(isinstance(i, str) and i for i in ids):
            raise InputError(f"{pid}: expected a list of face / edge / part IDs.")
        return query_param(pid, ids)

    options = list(parameter.get("options") or parameter.get("enumOptions") or [])
    enum_base = {k: parameter[k] for k in ("enumName", "namespace") if parameter.get(k)}
    if kind == "enum":
        if not isinstance(value, str):
            raise InputError(f"{pid}: expected one of {options}.")
        _check_options(pid, [value], options)
        return {"btType": "BTMParameterEnum-145", **enum_base, **base, "value": value}
    values = [value] if isinstance(value, str) else value  # multi_enum
    if not isinstance(values, list) or not all(isinstance(v, str) for v in values):
        raise InputError(f"{pid}: expected a list of {options}.")
    _check_options(pid, values, options)
    return {"btType": "BTMParameterMultiEnum-3411", **enum_base, **base, "values": values}


def _check_options(pid: str, chosen: list[str], options: list[str]) -> None:
    bad = [v for v in chosen if options and v not in options]
    if bad:
        raise InputError(f"{pid}: {bad} is not an option. Choose from {options}.")


def build_custom_feature(
    spec: dict[str, Any],
    *,
    name: str | None,
    values: dict[str, ParameterValue],
    units: UnitSystem,
) -> dict[str, Any]:
    """Build the ``BTFeatureDefinitionCall`` body that adds ``spec``'s feature with ``values``.

    The namespace is copied from the spec untouched. Parameters the caller does not mention keep
    the spec's default value, exactly as in the UI.
    """
    feature_type = spec.get("featureType")
    where = str(spec.get("namespace") or "")
    if not feature_type:
        raise InputError("The feature spec has no featureType.")
    if not where:
        raise InputError(
            f"{feature_type!r} is a built-in Onshape feature (it has no namespace). Use the "
            f"dedicated tools (extrude, fillet, ...) or add_feature for those."
        )

    specs = parameter_specs(spec)
    unknown = sorted(set(values) - set(specs))
    if unknown:
        raise InputError(
            f"{feature_type} has no parameter(s) {unknown}. Available: {sorted(specs)}."
        )

    parameters: list[dict[str, Any]] = []
    for pid, parameter in specs.items():
        if pid in values:
            kind = _KINDS.get(_kind_name(parameter))
            if kind is None:
                raise InputError(
                    f"{pid}: parameters of type "
                    f"{_kind_name(parameter).removeprefix('BTParameterSpec') or 'unknown'} cannot "
                    f"be set with add_custom_feature. Copy an existing feature's JSON and use "
                    f"add_feature instead."
                )
            parameters.append(_set_value(pid, kind, parameter, values[pid], units))
        elif _kind_name(parameter) not in _UI_ONLY and (default := _fresh_default(pid, parameter)):
            parameters.append(default)

    return {
        "btType": "BTFeatureDefinitionCall-1406",
        "feature": {
            "btType": "BTMFeature-134",
            "featureType": feature_type,
            # The UI stores the script's name template (e.g. "#screwfriendlyname") and renders it.
            "name": name
            or spec.get("featureNameTemplate")
            or spec.get("featureTypeName")
            or feature_type,
            "suppressed": False,
            "namespace": where,
            "parameters": parameters,
        },
    }
