"""Tools that produce files or images: screenshots and exports."""

import asyncio
import base64
import json
import re
import time
from pathlib import Path
from typing import Annotated, Any, Literal

from mcp.server.mcpserver import Image, MCPServer
from pydantic import Field

from .. import configurations
from ..app import App
from ..client import q
from ..config import MAX_IMAGE_PX, MIN_IMAGE_PX
from ..errors import InputError, OnshapeError
from ..views import MAX_VIEWS, resolve_view
from .common import Configuration, DocumentId, ElementId, Registrar, WorkspaceId

#: Rough vision-token cost of an image: ~ (width * height) / 750 for common vision models.
PIXELS_PER_TOKEN = 750
EXPORT_POLL_SECONDS = 1.0
EXPORT_TIMEOUT_SECONDS = 120.0

ExportFormat = Literal["STEP", "STL", "PARASOLID", "IGES"]
_EXTENSIONS = {"STEP": "step", "STL": "stl", "PARASOLID": "x_t", "IGES": "igs"}


def _flatten_images(images: Any) -> list[str]:
    """``shadedviews`` returns base64 strings, sometimes nested one level deep."""
    flat: list[str] = []
    for item in images or []:
        if isinstance(item, list):
            flat.extend(s for s in item if isinstance(s, str))
        elif isinstance(item, str):
            flat.append(item)
    return flat


def _safe_stem(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", text).strip("._") or "export"


def _write(path: Path, data: bytes) -> str | None:
    """Write ``data``; return the path, or None if the filesystem is not writable."""
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return str(path)
    except OSError:
        return None


def register(mcp: MCPServer, app: App, tool: Registrar) -> None:
    # structured=False: the result mixes a JSON text block with image content blocks.
    @tool(title="Capture screenshot", read_only=True, structured=False)
    async def capture_screenshot(
        document_id: DocumentId,
        element_id: ElementId,
        workspace_id: WorkspaceId = None,
        views: Annotated[
            list[Literal["isometric", "front", "back", "left", "right", "top", "bottom"]],
            Field(
                min_length=1,
                max_length=MAX_VIEWS,
                description="Camera angles to render. Every extra view is an extra image (and, "
                "if review is on, extra tokens).",
            ),
        ] = ["isometric"],  # noqa: B006 - never mutated; schema default
        part_id: Annotated[
            str | None, Field(description="Render only this part (Part Studios only).")
        ] = None,
        configuration: Configuration | None = None,
        width: Annotated[
            int | None,
            Field(
                ge=MIN_IMAGE_PX,
                le=MAX_IMAGE_PX,
                description="Image width in pixels (default from server settings).",
            ),
        ] = None,
        height: Annotated[
            int | None,
            Field(
                ge=MIN_IMAGE_PX,
                le=MAX_IMAGE_PX,
                description="Image height in pixels (default from server settings).",
            ),
        ] = None,
        show_edges: Annotated[bool, Field(description="Draw visible edges.")] = True,
        review: Annotated[
            bool | None,
            Field(
                description="true: send the image(s) to you so you can look at them (costs "
                "tokens, ~width*height/750 per image). false: only save them to disk and return "
                "the file paths (no image tokens). Omit to use the server's "
                "ONSHAPE_SCREENSHOT_REVIEW setting."
            ),
        ] = None,
    ) -> list[Any]:
        """Render a shaded screenshot of a Part Studio or Assembly from one or more standard
        camera angles. Images are always saved to disk. Whether they are also returned to you for
        visual inspection depends on the `review` flag / server setting - with review off you get
        only paths and metadata, which keeps token use low. Use it to visually verify what you
        built (shape, hole placement, orientation)."""
        settings = app.settings
        send_to_model = settings.screenshot_review if review is None else review
        wid = await app.workspace(document_id, workspace_id)
        element_type = await app.element_type(document_id, wid, element_id)
        base = f"{q(document_id)}/w/{q(wid)}/e/{q(element_id)}"
        if part_id:
            if element_type != "PARTSTUDIO":
                raise InputError("part_id is only supported for Part Studios.")
            path = f"/parts/d/{base}/partid/{q(part_id)}/shadedviews"
        elif element_type == "PARTSTUDIO":
            path = f"/partstudios/d/{base}/shadedviews"
        elif element_type == "ASSEMBLY":
            path = f"/assemblies/d/{base}/shadedviews"
        else:
            raise InputError(f"Screenshots need a Part Studio or Assembly; this is {element_type}.")

        out_w, out_h = width or settings.screenshot_width, height or settings.screenshot_height
        stamp = time.strftime("%Y%m%d-%H%M%S")
        saved: list[dict[str, Any]] = []
        images: list[Image] = []
        for view in dict.fromkeys(views):  # de-duplicate, keep order
            params: dict[str, Any] = {
                "viewMatrix": resolve_view(view),
                "outputWidth": out_w,
                "outputHeight": out_h,
                "pixelSize": 0,
                "edges": "show" if show_edges else "hide",
                "useAntiAliasing": True,
            }
            if not part_id:
                params["showAllParts"] = True
            if configuration:
                params["configuration"] = configurations.encode(configuration, app.units)
            data = await app.client.get(path, params)
            encoded = _flatten_images(data.get("images"))
            if not encoded:
                raise OnshapeError(
                    502,
                    "Onshape returned no image. The element may be empty - model something first.",
                )
            png = base64.b64decode(encoded[0])
            file_name = f"{_safe_stem(element_id)}_{view}_{stamp}_{time.monotonic_ns() % 10**6}.png"
            saved.append(
                {
                    "view": view,
                    "path": _write(settings.screenshot_dir / file_name, png),
                    "bytes": len(png),
                }
            )
            images.append(Image(data=png, format="png"))

        summary = {
            "element_type": element_type,
            "size_px": [out_w, out_h],
            "images": saved,
            "sent_to_model": send_to_model,
        }
        if send_to_model:
            summary["approx_image_tokens"] = len(images) * max(1, out_w * out_h // PIXELS_PER_TOKEN)
            return [json.dumps(summary), *images]
        summary["note"] = (
            "Screenshot review is off, so the image was not sent to the model (saves tokens). "
            "Pass review=true or set ONSHAPE_SCREENSHOT_REVIEW=true to look at it."
        )
        return [json.dumps(summary)]

    @tool(title="Export model to file", read_only=True)
    async def export_model(
        document_id: DocumentId,
        element_id: ElementId,
        workspace_id: WorkspaceId = None,
        format: Annotated[ExportFormat, Field(description="Output file format.")] = "STEP",
        part_ids: Annotated[
            list[str] | None,
            Field(description="Export only these parts (Part Studios only). Default: everything."),
        ] = None,
        filename: Annotated[
            str | None,
            Field(
                max_length=100,
                description="File name without directory (default: the element ID). Saved under "
                "the server's output directory.",
            ),
        ] = None,
    ) -> dict[str, Any]:
        """Export a Part Studio or Assembly to a CAD/mesh file (STEP, STL, Parasolid, IGES) on the
        machine running this server and return the file path. Does not modify the document. STL
        sizes follow the configured unit system."""
        settings = app.settings
        wid = await app.workspace(document_id, workspace_id)
        element_type = await app.element_type(document_id, wid, element_id)
        if element_type not in {"PARTSTUDIO", "ASSEMBLY"}:
            raise InputError(
                f"Only Part Studios and Assemblies can be exported; this is {element_type}."
            )
        if part_ids and element_type != "PARTSTUDIO":
            raise InputError("part_ids is only supported for Part Studios.")
        collection = "partstudios" if element_type == "PARTSTUDIO" else "assemblies"
        base = f"{q(document_id)}/w/{q(wid)}/e/{q(element_id)}"

        if format == "STL" and element_type == "PARTSTUDIO":
            data: bytes = await app.client.get(
                f"/partstudios/d/{base}/stl",
                {
                    "mode": "binary",
                    "grouping": True,
                    # Lowercase on purpose: the live API rejects the uppercase spelling that
                    # its own OpenAPI spec documents (verified against Onshape, 400 otherwise).
                    "units": "millimeter" if settings.units.length_unit == "mm" else "inch",
                    "partIds": ",".join(part_ids) if part_ids else None,
                },
                expect="bytes",
            )
        else:
            body: dict[str, Any] = {
                "formatName": format,
                "storeInDocument": False,
                "destinationName": _safe_stem(filename or element_id),
            }
            if part_ids:
                body["partIds"] = ",".join(part_ids)
            job = await app.client.post(f"/{collection}/d/{base}/translations", body)
            data = await _download_translation(app, document_id, job)

        extension = _EXTENSIONS[format]
        if data[:2] == b"PK":  # Onshape zipped a multi-file result
            extension = "zip"
        file_name = f"{_safe_stem(filename or element_id)}.{extension}"
        written = _write(settings.export_dir / file_name, data)
        if written is None:
            raise OnshapeError(
                500,
                f"Exported {len(data)} bytes but could not write to {settings.export_dir}. "
                f"Set ONSHAPE_OUTPUT_DIR to a writable directory.",
            )
        return {"path": written, "bytes": len(data), "format": format}


async def _download_translation(app: App, document_id: str, job: dict[str, Any]) -> bytes:
    translation_id = job.get("id")
    if not translation_id:
        raise OnshapeError(502, f"Onshape did not start an export job: {job!r}")
    deadline = time.monotonic() + EXPORT_TIMEOUT_SECONDS
    state = job
    while state.get("requestState") == "ACTIVE":
        if time.monotonic() > deadline:
            raise OnshapeError(
                504,
                f"Export still running after {EXPORT_TIMEOUT_SECONDS:g}s (job {translation_id}).",
            )
        await asyncio.sleep(EXPORT_POLL_SECONDS)
        state = await app.client.get(f"/translations/{q(translation_id)}")
    if state.get("requestState") != "DONE":
        raise OnshapeError(
            500, f"Export failed: {state.get('failureReason') or state.get('requestState')}"
        )
    external_ids = state.get("resultExternalDataIds") or []
    if not external_ids:
        raise OnshapeError(502, "Export finished but produced no file.")
    return await app.client.get(
        f"/documents/d/{q(document_id)}/externaldata/{q(external_ids[0])}", expect="bytes"
    )
