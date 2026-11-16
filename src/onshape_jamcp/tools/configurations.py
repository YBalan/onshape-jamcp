"""Configuration inputs of a Part Studio (Size M10 / M18, a Length, ...)."""

from typing import Annotated, Any, Literal

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from .. import configurations
from ..app import App
from ..client import q
from ..errors import InputError
from .common import DocumentId, ElementId, Registrar, WorkspaceId


def register(mcp: MCPServer, app: App, tool: Registrar) -> None:
    def path(document_id: str, workspace_id: str, element_id: str) -> str:
        return f"/elements/d/{q(document_id)}/w/{q(workspace_id)}/e/{q(element_id)}/configuration"

    def describe(parameter: dict[str, Any]) -> dict[str, Any]:
        # Inputs made in Onshape's UI have a generated ID that differs from the label shown
        # there; list the label (what people and the other tools use) and the ID beside it.
        entry: dict[str, Any] = {"name": configurations.input_label(parameter)}
        if parameter.get("parameterId") and parameter["parameterId"] != entry["name"]:
            entry["id"] = parameter["parameterId"]
        if configurations.is_enum(parameter):
            options = parameter.get("options") or []
            entry.update(
                {
                    "type": "list",
                    "options": [
                        configurations.option_label(parameter, o.get("option")) for o in options
                    ],
                    "default": configurations.option_label(
                        parameter, parameter.get("defaultValue")
                    ),
                }
            )
            ids = {
                configurations.option_label(parameter, o.get("option")): o.get("option")
                for o in options
            }
            if any(label != option_id for label, option_id in ids.items()):
                entry["option_ids"] = ids
        elif "Quantity" in str(parameter.get("btType")):
            quantity = parameter.get("rangeAndDefault") or {}
            kind = str(parameter.get("quantityType") or "REAL").lower()
            entry["type"] = {"real": "number"}.get(kind, kind)
            if entry["type"] == "length":  # Onshape stores meters: show the configured unit
                entry.update(
                    {
                        "minimum": app.units.from_meters(quantity.get("minValue", 0)),
                        "maximum": app.units.from_meters(quantity.get("maxValue", 0)),
                        "default": app.units.from_meters(quantity.get("defaultValue", 0)),
                        "unit": app.units.length_unit,
                    }
                )
            else:
                entry.update(
                    {
                        k: quantity.get(v)
                        for k, v in (
                            ("minimum", "minValue"),
                            ("maximum", "maxValue"),
                            ("default", "defaultValue"),
                        )
                    }
                )
        else:
            entry["type"] = str(parameter.get("btType", "")).split("-")[0].lower()
        return entry

    @tool(title="Get configuration inputs", read_only=True)
    async def get_configuration(
        document_id: DocumentId, element_id: ElementId, workspace_id: WorkspaceId = None
    ) -> dict[str, Any]:
        """List the configuration inputs of a Part Studio (for example a 'Size' list with M10 and
        M18, or a 'Length'), with their options and defaults. Names are the labels shown in
        Onshape; for an input made in Onshape's UI the generated `id` (and `option_ids`) are shown
        beside them. Other tools take a `configuration` such as {"Size": "M18"} (labels or IDs)
        to measure or look at one variant."""
        wid = await app.workspace(document_id, workspace_id)
        data = await app.client.get(path(document_id, wid, element_id))
        return {"inputs": [describe(p) for p in data.get("configurationParameters") or []]}

    @tool(title="Add or change a configuration input", writes=True, idempotent=True)
    async def set_configuration_input(
        document_id: DocumentId,
        element_id: ElementId,
        name: Annotated[
            str,
            Field(
                min_length=1,
                max_length=60,
                pattern=r"^[A-Za-z_][A-Za-z0-9_]*$",
                description="Input name, e.g. 'Size' or 'Length'. Number inputs are usable in "
                "expressions as '#Length'.",
            ),
        ],
        kind: Annotated[
            Literal["list", "length", "angle", "number"], Field(description="Type of input.")
        ] = "list",
        options: Annotated[
            list[str] | None,
            Field(description="For a list: the choices, e.g. ['M10', 'M18'] (at least two)."),
        ] = None,
        default: Annotated[
            str | float | int | None,
            Field(
                description="List: the default option (first one if omitted). Number inputs: "
                "the default value (length in the configured unit, degrees for an angle)."
            ),
        ] = None,
        minimum: Annotated[float | None, Field(description="Number inputs: lowest value.")] = None,
        maximum: Annotated[float | None, Field(description="Number inputs: highest value.")] = None,
        workspace_id: WorkspaceId = None,
    ) -> dict[str, Any]:
        """Add a configuration input to a Part Studio, or replace the input of the same name. Only
        when the user asks for configurations. To make dimensions depend on a list input, use
        set_variable with `by_option`."""
        wid = await app.workspace(document_id, workspace_id)
        if kind == "list":
            if not options:
                raise InputError("A list input needs `options`, e.g. ['M10', 'M18'].")
            chosen = str(default) if default is not None else options[0]
            parameter = configurations.enum_input(name, options, chosen)
        else:
            if default is None or minimum is None or maximum is None:
                raise InputError("A number input needs `default`, `minimum` and `maximum`.")
            parameter = configurations.quantity_input(
                name, kind, float(default), minimum, maximum, app.units
            )
        base = path(document_id, wid, element_id)
        existing = (await app.client.get(base)).get("configurationParameters") or []
        merged = [
            p for p in existing if name not in (p.get("parameterId"), p.get("parameterName"))
        ] + [parameter]
        await app.client.post(
            base,
            {
                "btType": "BTConfigurationUpdateCall-2933",
                "configurationParameters": merged,
                "currentConfiguration": configurations.current_for(merged),
            },
        )
        app.forget_configuration(document_id, wid, element_id)
        data = await app.client.get(base)
        return {"inputs": [describe(p) for p in data.get("configurationParameters") or []]}
