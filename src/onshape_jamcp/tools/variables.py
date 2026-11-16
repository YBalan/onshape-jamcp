"""Part Studio variables: the named dimensions models should be built from."""

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from .. import configurations, features, fs
from ..app import App
from ..client import q
from ..errors import InputError, OnshapeError
from .common import DocumentId, ElementId, Registrar, WorkspaceId


def register(mcp: MCPServer, app: App, tool: Registrar) -> None:
    def features_path(document_id: str, workspace_id: str, element_id: str) -> str:
        return f"/partstudios/d/{q(document_id)}/w/{q(workspace_id)}/e/{q(element_id)}/features"

    def expression_builder(kind: str):
        units = app.units
        return {
            "length": units.length_expression,
            "angle": units.angle_expression,
            "number": units.number_expression,
        }[kind]

    async def set_many(
        document_id: str,
        element_id: str,
        values: dict[str, float | int | str],
        kind: str,
        workspace_id: str | None,
    ) -> dict[str, Any]:
        """Several variables with one read of the feature list: existing ones are changed by a
        single ``features/updates`` request (checked live: two variables, one request, the model
        followed); new ones are created one by one (Onshape cannot add features in bulk)."""
        for name in values:
            if not fs.is_identifier(name):
                raise InputError(f"{name!r} is not a valid variable name (letters, digits, _).")
        wid = await app.workspace(document_id, workspace_id)
        existing = {v["name"]: v for v in await app.variables(document_id, wid, element_id)}
        base = features_path(document_id, wid, element_id)
        results: dict[str, dict[str, Any]] = {}
        updates, creates = [], []
        for name, value in values.items():
            current = existing.get(name)
            own_kind = current["kind"].lower() if current else kind
            expression = expression_builder(own_kind)(value)
            (updates if current else creates).append((name, own_kind, expression, current))
        if updates:
            structs = [
                {
                    "btType": "BTMFeature-134",
                    "featureId": current["feature_id"],
                    "parameters": [
                        features.quantity_param(
                            features.VARIABLE_VALUE_PARAMETER[own_kind.upper()], expression
                        )
                    ],
                }
                for _, own_kind, expression, current in updates
            ]
            response = await app.client.post(
                f"{base}/updates",
                {
                    "btType": "BTUpdateFeaturesCall-1748",
                    "features": structs,
                    "updateSuppressionAttributes": False,
                },
            )
            states = response.get("featureStates") or {}
            for name, _, expression, current in updates:
                status = (states.get(current["feature_id"]) or {}).get("featureStatus", "UNKNOWN")
                results[name] = {
                    "variable": name,
                    "expression": expression,
                    "created": False,
                    "status": status,
                }
        for name, own_kind, expression, _ in creates:
            reply = features.summarize_feature_response(
                await app.client.post(base, features.variable_feature(name, own_kind, expression))
            )
            results[name] = {
                "variable": name,
                "expression": expression,
                "created": True,
                "status": reply["status"],
                "feature_id": reply["feature_id"],
            }
        return {
            "variables": [results[n] for n in values],
            "onshape_requests": 1 + (1 if updates else 0) + len(creates),
        }

    @tool(title="List variables", read_only=True)
    async def list_variables(
        document_id: DocumentId, element_id: ElementId, workspace_id: WorkspaceId = None
    ) -> dict[str, Any]:
        """List the variables of a Part Studio: name, type, the expression it was set with and its
        current value (lengths in the configured unit, angles in degrees). A variable that depends
        on a configuration input also shows configured_by and its value per option. Reference a
        variable as '#name' in sketch sizes, extrude depths, fillet radii, thread pitch, etc."""
        wid = await app.workspace(document_id, workspace_id)
        found = await app.variables(document_id, wid, element_id)
        inputs: list[dict[str, Any]] = []
        if any(v.get("configured_by") for v in found):
            try:  # show the labels people see in Onshape rather than generated IDs
                inputs = await app.configuration_parameters(document_id, wid, element_id)
            except OnshapeError:
                inputs = []
        values = (
            await app.variable_values(
                document_id, wid, element_id, {v["name"]: v["kind"] for v in found}
            )
            if found
            else {}
        )
        units = app.units
        out = []
        for v in found:
            entry: dict[str, Any] = {
                "name": v["name"],
                "type": v["kind"].lower(),
                "expression": v["expression"],
                "feature_id": v["feature_id"],
            }
            if v.get("configured_by"):
                spec = configurations.find_input(inputs, v["configured_by"], enum_only=True)
                entry["configured_by"] = (
                    configurations.input_label(spec) if spec else v["configured_by"]
                )
                entry["by_option"] = (
                    {configurations.option_label(spec, o): e for o, e in v["by_option"].items()}
                    if spec and v["by_option"]
                    else v["by_option"]
                )
            value = values.get(v["name"])
            if value is not None:
                if v["kind"] == "LENGTH":
                    entry["value"] = units.from_meters(value)  # the script returns meters
                    entry["unit"] = units.length_unit
                else:
                    entry["value"] = round(value, 6)
                    entry["unit"] = "deg" if v["kind"] == "ANGLE" else None
            out.append(entry)
        return {"variables": out}

    @tool(title="Set a variable", writes=True, idempotent=True)
    async def set_variable(
        document_id: DocumentId,
        element_id: ElementId,
        name: Annotated[
            str | None,
            Field(
                min_length=1,
                max_length=60,
                pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
                description="Variable name: letters, digits and underscores, e.g. "
                "'shank_diameter'. Referenced as '#shank_diameter'. Omit when using `variables`.",
            ),
        ] = None,
        value: Annotated[
            float | int | str | None,
            Field(
                description="A number (length in the configured unit, degrees for an angle), a "
                "string with a unit ('1.5 mm'), or an expression using other variables "
                "('#shank_diameter / 2'). Omit when using by_option."
            ),
        ] = None,
        kind: Annotated[
            Literal["length", "angle", "number"],
            Field(description="Quantity type; only used when the variable is created."),
        ] = "length",
        by_option: Annotated[
            dict[str, dict[str, float | int | str]] | None,
            Field(
                description="Make the value depend on a configuration LIST input: "
                "{'Size': {'M10': 10, 'M18': 18}} gives 10 for M10 and 18 for M18. Every option "
                "of the input needs a value. Create the input first with "
                "set_configuration_input. Only when the user asks for configurations."
            ),
        ] = None,
        variables: Annotated[
            dict[str, float | int | str] | None,
            Field(
                description="Set SEVERAL variables at once, e.g. {'d': 10, 'pitch': 1.5, "
                "'head_h': 6.4}: existing ones change in ONE Onshape request, new ones are "
                "created. Instead of name / value."
            ),
        ] = None,
        workspace_id: WorkspaceId = None,
    ) -> dict[str, Any]:
        """Create a Part Studio variable, or change its value if it already exists. By default
        model with variables: put every key dimension (diameter, length, pitch, wall thickness,
        ...) in a variable first, then use '#name' wherever the size is needed, so changing one
        value reshapes the whole model. Pass `variables` to set many in one call."""
        if variables is not None:
            if name is not None or value is not None or by_option is not None:
                raise InputError("`variables` cannot be combined with name, value or by_option.")
            return await set_many(document_id, element_id, variables, kind, workspace_id)
        if name is None:
            raise InputError("Give a variable `name` (or `variables` for several).")
        if (value is None) == (by_option is None):
            raise InputError("Give either `value` or `by_option` (not both, not neither).")
        wid = await app.workspace(document_id, workspace_id)
        existing = next(
            (v for v in await app.variables(document_id, wid, element_id) if v["name"] == name),
            None,
        )
        effective_kind = existing["kind"].lower() if existing else kind
        to_expression = expression_builder(effective_kind)
        value_parameter = features.VARIABLE_VALUE_PARAMETER[effective_kind.upper()]

        if by_option is not None:
            if len(by_option) != 1:
                raise InputError("by_option takes exactly one input: {'Size': {'M10': 10, ...}}.")
            ((input_name, per_option),) = by_option.items()
            configuration = await app.client.get(
                f"/elements/d/{q(document_id)}/w/{q(wid)}/e/{q(element_id)}/configuration"
            )
            spec = configurations.find_input(
                configuration.get("configurationParameters") or [], input_name, enum_only=True
            )
            if spec is None:
                raise InputError(
                    f"There is no configuration list input {input_name!r} in this Part Studio. "
                    "Create it with set_configuration_input first."
                )
            expressions = {option: to_expression(v) for option, v in per_option.items()}
            replacement = configurations.configured_parameter(
                value_parameter, input_name, spec, expressions
            )
            shown = ", ".join(f"{o}={e}" for o, e in expressions.items())
            expression = f"by {input_name}: {shown}"
        else:
            assert value is not None
            expression = to_expression(value)
            replacement = features.quantity_param(value_parameter, expression)

        base = features_path(document_id, wid, element_id)
        if existing is None:
            body = features.variable_feature(name, effective_kind, "0 mm")
            body["feature"]["parameters"] = [
                replacement if p["parameterId"] == value_parameter else p
                for p in body["feature"]["parameters"]
            ]
            response = await app.client.post(base, body)
            created = True
        else:
            feature = existing["feature"]
            feature["parameters"] = [
                replacement if p.get("parameterId") == value_parameter else p
                for p in feature["parameters"]
            ]
            response = await app.client.post(
                f"{base}/featureid/{q(existing['feature_id'])}",
                {"btType": "BTFeatureDefinitionCall-1406", "feature": feature},
            )
            created = False
        # No read-back of the feature list: Onshape limits that endpoint per day (a 429 with a
        # 22 h Retry-After was seen live), and the POST reply is reliable for variables.
        result = features.summarize_feature_response(response)
        result.update({"variable": name, "expression": expression, "created": created})
        if result["status"] != "OK":
            result["warning"] = (
                "Onshape reports this variable as "
                f"{result['status']}. Check the value or expression (a variable it uses may be "
                "missing)."
            )
        return result
