"""Read-only inspection of Part Studios and Assemblies."""

from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from pydantic import Field

from .. import configurations, fs, names
from ..app import App
from ..client import q
from ..errors import InputError, OnshapeError
from ..units import UnitSystem
from .common import Configuration, DocumentId, ElementId, Registrar, WorkspaceId

MAX_FACES = 300
MAX_EDGES = 600


def _xyz(value: Any) -> list[float] | None:
    if isinstance(value, dict) and all(isinstance(value.get(k), int | float) for k in "xyz"):
        return [float(value[k]) for k in "xyz"]
    if (
        isinstance(value, list | tuple)
        and len(value) >= 3
        and all(isinstance(c, int | float) for c in value[:3])
    ):
        return [float(c) for c in value[:3]]
    return None


def _first_number(value: Any) -> float | None:
    """Onshape returns many scalars as ``[value, lower, upper]`` arrays."""
    if isinstance(value, list | tuple):
        value = value[0] if value else None
    return float(value) if isinstance(value, int | float) and not isinstance(value, bool) else None


def summarize_face(face: dict[str, Any], units: UnitSystem) -> dict[str, Any]:
    surface = face.get("surface") or {}
    item: dict[str, Any] = {
        "id": face.get("id"),
        "type": str(surface.get("type") or surface.get("btType") or "unknown").lower(),
    }
    if isinstance(face.get("area"), int | float):
        item["area"] = units.area_from_m2(face["area"])
    origin = _xyz(surface.get("origin"))
    if origin:
        item["origin"] = units.point_from_meters(origin)
    normal = _xyz(surface.get("normal"))
    if normal:
        if face.get("orientation") is False:  # face is reversed relative to its surface
            normal = [-c for c in normal]
        item["outward_normal"] = [round(c, 4) + 0.0 for c in normal]
    if isinstance(surface.get("radius"), int | float):
        item["radius"] = units.from_meters(surface["radius"])
    return item


def summarize_edge(
    edge: dict[str, Any], vertices: dict[str, list[float]], units: UnitSystem
) -> dict[str, Any]:
    curve = edge.get("curve") or {}
    item: dict[str, Any] = {
        "id": edge.get("id"),
        "type": str(curve.get("type") or curve.get("btType") or "unknown").lower(),
    }
    ends = [vertices.get(v) for v in (edge.get("vertices") or [])]
    if ends and all(ends):
        item["start"] = units.point_from_meters(ends[0])
        item["end"] = units.point_from_meters(ends[-1])
    if isinstance(curve.get("radius"), int | float):
        item["radius"] = units.from_meters(curve["radius"])
    return item


def register(mcp: MCPServer, app: App, tool: Registrar) -> None:
    @tool(title="List features", read_only=True)
    async def list_features(
        document_id: DocumentId,
        element_id: ElementId,
        workspace_id: WorkspaceId = None,
        feature_id: Annotated[
            str | None,
            Field(
                description="If set, return the full raw JSON of just this feature (useful as a "
                "template for add_feature or to see its parameter IDs for update_feature)."
            ),
        ] = None,
    ) -> dict[str, Any]:
        """List the feature tree of a Part Studio in order: feature ID, name, type (newSketch,
        extrude, fillet, ...) and health status (OK / WARNING / ERROR). Check this after creating
        features - an ERROR status means the feature failed to build."""
        wid = await app.workspace(document_id, workspace_id)
        data = await app.client.get(
            f"/partstudios/d/{q(document_id)}/w/{q(wid)}/e/{q(element_id)}/features"
        )
        features = data.get("features") or []
        states = data.get("featureStates") or {}
        if feature_id:
            for feature in features:
                if feature.get("featureId") == feature_id:
                    return {"feature": feature}
            raise InputError(f"No feature with ID {feature_id!r}. Call list_features without it.")
        return {
            "features": [
                {
                    "feature_id": f.get("featureId"),
                    "name": f.get("name"),
                    "feature_type": f.get("featureType"),
                    "suppressed": bool(f.get("suppressed")),
                    "status": (states.get(f.get("featureId")) or {}).get("featureStatus"),
                }
                for f in features
            ],
            "rollback_index": data.get("rollbackIndex"),
        }

    @tool(title="List parts", read_only=True)
    async def list_parts(
        document_id: DocumentId, element_id: ElementId, workspace_id: WorkspaceId = None
    ) -> dict[str, Any]:
        """List the parts (solid/surface bodies) in a Part Studio with their part IDs, which
        other tools accept to focus on one part. A part flagged has_default_name is still called
        "Part N": give it a descriptive name with `rename`."""
        wid = await app.workspace(document_id, workspace_id)
        entries = []
        for p in await app.parts(document_id, wid, element_id):
            entry = {
                "part_id": p.get("partId"),
                "name": p.get("name"),
                "body_type": p.get("bodyType"),
                "hidden": bool(p.get("isHidden")),
            }
            if names.is_default_part_name(p.get("name")):
                entry["has_default_name"] = True
            entries.append(entry)
        return {"parts": entries}

    @tool(title="Measure part or Part Studio", read_only=True)
    async def get_part_properties(
        document_id: DocumentId,
        element_id: ElementId,
        workspace_id: WorkspaceId = None,
        part_id: Annotated[
            str | None, Field(description="Measure only this part (from list_parts).")
        ] = None,
        configuration: Configuration | None = None,
        tight: Annotated[
            bool,
            Field(
                description="Use Onshape's TIGHT bounding box (default). The quick REST box can "
                "be too large on swept surfaces such as threads (0.5 mm per side on an M10 "
                "nut); false returns that quick box."
            ),
        ] = True,
    ) -> dict[str, Any]:
        """Measure geometry: the axis-aligned bounding box (min/max/size) plus volume, surface
        area, mass and centroid per part, in the configured length unit. Use it to verify
        dimensions and orientation after modeling. Mass is only meaningful if the part has a
        material assigned in Onshape."""
        configurations.encode(configuration, app.units)  # validate before any request
        wid = await app.workspace(document_id, workspace_id)
        cfg = await app.configuration_param(document_id, wid, element_id, configuration)
        units = app.units
        base = f"/partstudios/d/{q(document_id)}/w/{q(wid)}/e/{q(element_id)}"
        if part_id:
            base = f"/parts/d/{q(document_id)}/w/{q(wid)}/e/{q(element_id)}/partid/{q(part_id)}"
        bbox = await app.client.get(f"{base}/boundingboxes", {"configuration": cfg})
        # Onshape evaluates all parts as ONE aggregated body unless told otherwise (massAsGroup
        # defaults to true), which hid the individual parts: ask for one entry per part.
        mass = await app.client.get(
            f"{base}/massproperties",
            {"configuration": cfg, "massAsGroup": None if part_id else False},
        )

        lows = [bbox.get(k) for k in ("lowX", "lowY", "lowZ")]
        highs = [bbox.get(k) for k in ("highX", "highY", "highZ")]
        result: dict[str, Any] = {"length_unit": units.length_unit}
        if tight:
            corners = None
            try:
                response = await app.evaluate_featurescript(
                    document_id,
                    wid,
                    element_id,
                    fs.tight_box_script(bool(part_id)),
                    {"part": [part_id]} if part_id else None,
                    cfg,
                )
                corners = fs.unwrap(response.get("result"))
            except OnshapeError:
                corners = None
            if isinstance(corners, list) and len(corners) == 6:
                lows, highs = corners[:3], corners[3:]
            else:
                result["bounding_box_note"] = "Tight box unavailable; this is the quick REST box."
        if all(isinstance(v, int | float) for v in lows + highs):
            result["bounding_box"] = {
                "min": units.point_from_meters(lows),
                "max": units.point_from_meters(highs),
                "size": units.point_from_meters(
                    [h - lo for lo, h in zip(lows, highs, strict=True)]
                ),
            }
        bodies = mass.get("bodies") if isinstance(mass, dict) and "bodies" in mass else {None: mass}
        parts = []
        for pid, info in (bodies or {}).items():
            volume, area, weight = (
                _first_number(info.get(k)) for k in ("volume", "periphery", "mass")
            )
            centroid = _xyz(info.get("centroid"))
            entry: dict[str, Any] = {"part_id": pid or part_id}
            if volume is not None:
                entry["volume"] = units.volume_from_m3(volume)
            if area is not None:
                entry["surface_area"] = units.area_from_m2(area)
            if weight is not None and info.get("hasMass", True):
                entry["mass_kg"] = round(weight, 6)
            if centroid:
                entry["centroid"] = units.point_from_meters(centroid)
            parts.append(entry)
        result["parts"] = parts
        volumes = [p["volume"] for p in parts if "volume" in p]
        if len(volumes) > 1:
            result["total_volume"] = sum(volumes)
        result["volume_unit"] = units.volume_unit
        result["area_unit"] = units.area_unit
        return result

    @tool(title="Get faces and edges", read_only=True)
    async def get_body_details(
        document_id: DocumentId,
        element_id: ElementId,
        workspace_id: WorkspaceId = None,
        part_id: Annotated[
            str | None, Field(description="Only this part (from list_parts).")
        ] = None,
        include_edges: Annotated[
            bool, Field(description="Also list edges (needed for fillet / chamfer).")
        ] = True,
        configuration: Configuration | None = None,
    ) -> dict[str, Any]:
        """List the faces (and optionally edges) of each part with their IDs, geometry type,
        area, a point on the face and its outward normal, all in the configured length unit. Use
        face IDs as `plane` in create_sketch to sketch on a face, and edge IDs for fillet /
        chamfer. Output is capped for very large parts."""
        configurations.encode(configuration, app.units)  # validate before any request
        wid = await app.workspace(document_id, workspace_id)
        units = app.units
        data = await app.client.get(
            f"/partstudios/d/{q(document_id)}/w/{q(wid)}/e/{q(element_id)}/bodydetails",
            {
                "includeGeometricData": True,
                "partIds": [part_id] if part_id else None,
                "configuration": await app.configuration_param(
                    document_id, wid, element_id, configuration
                ),
            },
        )
        bodies_out = []
        for body in data.get("bodies") or []:
            vertices = {
                v.get("id"): pt for v in body.get("vertices") or [] if (pt := _xyz(v.get("point")))
            }
            faces = body.get("faces") or []
            entry: dict[str, Any] = {
                "part_id": body.get("id"),
                "face_count": len(faces),
                "faces": [summarize_face(f, units) for f in faces[:MAX_FACES]],
            }
            if include_edges:
                edges = body.get("edges") or []
                entry["edge_count"] = len(edges)
                entry["edges"] = [summarize_edge(e, vertices, units) for e in edges[:MAX_EDGES]]
            entry["truncated"] = len(faces) > MAX_FACES or (
                include_edges and len(body.get("edges") or []) > MAX_EDGES
            )
            bodies_out.append(entry)
        return {"length_unit": units.length_unit, "bodies": bodies_out}

    @tool(title="Get assembly", read_only=True)
    async def get_assembly(
        document_id: DocumentId, element_id: ElementId, workspace_id: WorkspaceId = None
    ) -> dict[str, Any]:
        """Describe an Assembly: its instances (parts/sub-assemblies), where each is placed
        (translation in the configured length unit) and its mates."""
        wid = await app.workspace(document_id, workspace_id)
        units = app.units
        data = await app.client.get(
            f"/assemblies/d/{q(document_id)}/w/{q(wid)}/e/{q(element_id)}",
            {"includeMateFeatures": True, "excludeSuppressed": False},
        )
        root = data.get("rootAssembly") or {}
        placements = []
        for occ in root.get("occurrences") or []:
            matrix = occ.get("transform") or []
            entry: dict[str, Any] = {"path": occ.get("path"), "fixed": bool(occ.get("fixed"))}
            if len(matrix) >= 12 and all(isinstance(c, int | float) for c in matrix[:12]):
                entry["position"] = units.point_from_meters([matrix[3], matrix[7], matrix[11]])
            placements.append(entry)
        return {
            "length_unit": units.length_unit,
            "instances": [
                {
                    "instance_id": i.get("id"),
                    "name": i.get("name"),
                    "type": i.get("type"),
                    "part_id": i.get("partId"),
                    "element_id": i.get("elementId"),
                    "suppressed": bool(i.get("suppressed")),
                }
                for i in root.get("instances") or []
            ],
            "placements": placements,
            "mates": [
                {
                    "feature_id": f.get("id") or f.get("featureId"),
                    "name": (f.get("featureData") or {}).get("name") or f.get("name"),
                    "type": f.get("featureType"),
                    "suppressed": bool(f.get("suppressed")),
                }
                for f in root.get("features") or []
            ],
        }

    @tool(title="Evaluate FeatureScript", read_only=True)
    async def run_featurescript(
        document_id: DocumentId,
        element_id: ElementId,
        script: Annotated[
            str,
            Field(
                min_length=1,
                max_length=20000,
                description="A FeatureScript lambda, e.g. `function(context is Context, queries) "
                "{ return size(evaluateQuery(context, qEverything(EntityType.FACE))); }`.",
            ),
        ],
        workspace_id: WorkspaceId = None,
    ) -> dict[str, Any]:
        """Evaluate a read-only FeatureScript snippet against a Part Studio and return its
        result. Powerful for queries the other tools do not cover (face counts, distances,
        evaluating selections). It cannot modify the document. NOTE: lengths inside FeatureScript
        results are in METERS, not the configured unit."""
        wid = await app.workspace(document_id, workspace_id)
        response = await app.evaluate_featurescript(document_id, wid, element_id, script)
        return {
            "result": fs.unwrap(response.get("result")),
            "console": fs.unwrap(response.get("console")),
            "notices": response.get("notices"),
        }
