"""Live smoke test: drives onshape-jamcp end to end against a REAL Onshape account.

    uv run python scripts/smoke_test.py            # uses ONSHAPE_* from the environment / .env
    uv run python scripts/smoke_test.py --keep     # leave the test document in place
    uv run python scripts/smoke_test.py --public   # required on Onshape free plans
    uv run python scripts/smoke_test.py --custom-feature-studio <Feature Studio tab URL>

It creates a new document (private; public with --public), models a small part (plate, fillets,
hole), measures it, takes a screenshot, exports STEP/STL and exercises the remaining tools, then
moves the document to Onshape's trash (unless --keep).
It only ever touches the document it creates. Exit code 0 = everything verified.

Why this exists: the unit tests run against a fake Onshape. This script is what verifies the
assumptions only a real Onshape can confirm (plane orientation, face normals, extrude direction,
feature JSON accepted by the server).
"""

import argparse
import asyncio
import math
import os
import sys
import time
from pathlib import Path

from mcp import Client
from mcp_types import ImageContent, TextContent

from onshape_jamcp.config import Settings
from onshape_jamcp.server import build_server

RESULTS: list[tuple[bool, str, str]] = []


def check(ok: bool, name: str, detail: str = "") -> bool:
    RESULTS.append((ok, name, detail))
    print(f"  [{'PASS' if ok else 'FAIL'}] {name}" + (f"  - {detail}" if detail else ""))
    return ok


def load_env_file(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if line and not line.startswith("#") and "=" in line:
            key, _, value = line.partition("=")
            os.environ.setdefault(key.strip(), value.strip().strip("'\""))


class Session:
    def __init__(self, client: Client) -> None:
        self.client = client

    async def call(self, name: str, args: dict | None = None) -> dict:
        result = await self.client.call_tool(name, args or {})
        if result.is_error:
            text = " ".join(c.text for c in result.content if isinstance(c, TextContent))
            raise RuntimeError(f"{name} failed: {text}")
        return result.structured_content or {}


def close(a: float, b: float, tol: float = 0.05) -> bool:
    return math.isclose(a, b, abs_tol=tol)


async def run(keep: bool, public: bool = False, custom_studio: str | None = None) -> bool:
    settings = Settings.from_env(os.environ)
    if not settings.has_credentials:
        print("Set ONSHAPE_ACCESS_KEY and ONSHAPE_SECRET_KEY (or put them in .env).")
        return False
    settings = Settings.from_env({**os.environ, "ONSHAPE_SCREENSHOT_REVIEW": "false"})
    server = build_server(settings)
    unit = settings.units.length_unit
    print(
        f"onshape-jamcp live smoke test  (units: {settings.units.name}, base: {settings.base_url})"
    )
    if unit != "mm":
        print("  note: set ONSHAPE_UNITS=metric for this test; sizes below assume millimeters.")
        return False

    async with Client(server) as client:
        s = Session(client)
        print("\n1. Connection")
        who = await s.call("whoami")
        check(bool(who.get("connected")), "authenticated", f"user {who.get('name')}")

        print("\n2. Document + Part Studio")
        stamp = time.strftime("%Y-%m-%d %H:%M:%S")
        try:
            doc = await s.call(
                "create_document", {"name": f"jamcp smoke test {stamp}", "public": public}
            )
        except RuntimeError as exc:
            check(False, "create_document", str(exc))
            if "PUBLIC documents" in str(exc):
                print(
                    "\n  Your Onshape plan only allows public documents. Re-run with --public "
                    "to create a throw-away PUBLIC test document\n  (it contains only a test "
                    "plate and is moved to the trash at the end)."
                )
            return False
        did = doc["document_id"]
        check(bool(did), "create_document", did)
        studio = next((e for e in doc["elements"] if e["type"] == "PARTSTUDIO"), None)
        if studio is None:
            studio = await s.call("create_element", {"document_id": did, "name": "Smoke"})
            eid = studio["element_id"]
        else:
            eid = studio["element_id"]
        check(bool(eid), "part studio available", eid)
        base = {"document_id": did, "element_id": eid}

        try:
            await modeling(s, base, did, eid)
            await custom_features(s, base, custom_studio)
        except Exception as exc:  # noqa: BLE001 - report, then still clean up
            check(False, "run aborted by an error", str(exc))
        finally:
            if keep:
                print(f"\nKept document: {settings.base_url}/documents/{did}")
            else:
                try:
                    await server_cleanup(s, did)
                except Exception as exc:  # noqa: BLE001 - best effort
                    print(f"\n(could not trash the test document {did}: {exc})")

    failed = [r for r in RESULTS if not r[0]]
    print(f"\n{len(RESULTS) - len(failed)}/{len(RESULTS)} checks passed")
    for _, name, detail in failed:
        print(f"  FAILED: {name} {detail}")
    return not failed


async def server_cleanup(s: Session, did: str) -> None:
    await s.call("onshape_api_request", {"method": "DELETE", "path": f"/documents/{did}"})
    print(f"\nMoved test document {did} to Onshape's trash.")


async def modeling(s: Session, base: dict, did: str, eid: str) -> None:
    print("\n3. Plate on the Top plane (sketch 40 x 20 mm, extrude 10 mm)")
    sk = await s.call(
        "create_sketch",
        {
            **base,
            "plane": "Top",
            "name": "Plate sketch",
            "shapes": [{"type": "rectangle", "corner1": [0, 0], "corner2": [40, 20]}],
        },
    )
    check(sk["status"] == "OK", "sketch status OK", str(sk["status"]))
    ex = await s.call(
        "extrude", {**base, "sketch_feature_id": sk["feature_id"], "depth": 10, "name": "Plate"}
    )
    check(ex["status"] == "OK", "extrude status OK", str(ex["status"]))
    props = await s.call("get_part_properties", base)
    box = props.get("bounding_box", {})
    size, lo, hi = box.get("size", [0, 0, 0]), box.get("min", [0, 0, 0]), box.get("max", [0, 0, 0])
    check(
        all(close(a, b) for a, b in zip(sorted(size), [10, 20, 40], strict=True)),
        "plate has the requested millimeter dimensions",
        f"size={size}",
    )
    check(
        close(size[0], 40) and close(size[1], 20) and close(size[2], 10),
        "Top plane = XY, extrudes along Z (size is 40 x 20 x 10 in X,Y,Z)",
        f"min={lo} max={hi}",
    )
    print(f"      (extrude direction on Top: z from {lo[2]} to {hi[2]})")

    print("\n4. Other origin planes")
    for plane, expect_axes in (("Front", (0, 2)), ("Right", (1, 2))):
        sk2 = await s.call(
            "create_sketch",
            {
                **base,
                "plane": plane,
                "name": f"{plane} test",
                "shapes": [{"type": "rectangle", "corner1": [0, 0], "corner2": [5, 5]}],
            },
        )
        ex2 = await s.call(
            "extrude",
            {**base, "sketch_feature_id": sk2["feature_id"], "depth": 7, "name": f"{plane} bar"},
        )
        parts = (await s.call("list_parts", base))["parts"]
        pid = parts[-1]["part_id"]
        p = (await s.call("get_part_properties", {**base, "part_id": pid}))["bounding_box"]
        got = p["size"]
        in_plane = sorted(got[i] for i in expect_axes)
        normal_axis = ({0, 1, 2} - set(expect_axes)).pop()
        check(
            ex2["status"] == "OK"
            and all(close(v, 5) for v in in_plane)
            and close(got[normal_axis], 7),
            f"{plane} plane orientation (5 x 5 in-plane, 7 along the normal axis)",
            f"size={got}, normal axis={'XYZ'[normal_axis]}, range {p['min']}..{p['max']}",
        )
        await s.call("delete_feature", {**base, "feature_id": ex2["feature_id"]})
        await s.call("delete_feature", {**base, "feature_id": sk2["feature_id"]})

    print("\n5. Faces, edges and fillets")
    details = await s.call("get_body_details", base)
    body = details["bodies"][0]
    check(body["face_count"] == 6, "plate has 6 faces", str(body["face_count"]))
    faces = body["faces"]
    top = max(
        (f for f in faces if f.get("outward_normal") == [0.0, 0.0, 1.0]),
        key=lambda f: f["origin"][2],
        default=None,
    )
    check(
        top is not None and close(top["origin"][2], 10),
        "top face has outward normal +Z at z=10",
        str(top),
    )
    bottom = [f for f in faces if f.get("outward_normal") == [0.0, 0.0, -1.0]]
    check(len(bottom) == 1, "bottom face has outward normal -Z", str(bottom))
    vertical = [
        e
        for e in body["edges"]
        if e.get("start")
        and e.get("end")
        and close(e["start"][0], e["end"][0])
        and close(e["start"][1], e["end"][1])
        and not close(e["start"][2], e["end"][2])
    ]
    check(len(vertical) == 4, "found the 4 vertical edges", str(len(vertical)))
    fil = await s.call(
        "fillet", {**base, "edge_ids": [e["id"] for e in vertical], "radius": 3, "name": "Corners"}
    )
    check(fil["status"] == "OK", "fillet status OK", str(fil["status"]))

    print("\n6. Hole through a face sketch (and extrude direction)")
    details = await s.call("get_body_details", {**base, "include_edges": False})
    top = max(
        (f for f in details["bodies"][0]["faces"] if f.get("outward_normal") == [0.0, 0.0, 1.0]),
        key=lambda f: f["origin"][2],
    )
    before = (await s.call("get_part_properties", base))["parts"][0]["volume"]
    face_sketch = await s.call(
        "create_sketch",
        {
            **base,
            "plane": top["id"],
            "name": "Hole sketch",
            "shapes": [{"type": "circle", "center": [20, 10], "diameter": 8}],
        },
    )
    check(face_sketch["status"] == "OK", "sketch on a face accepted", str(face_sketch["status"]))
    cut = await s.call(
        "extrude",
        {
            **base,
            "sketch_feature_id": face_sketch["feature_id"],
            "depth": 10,
            "operation": "remove",
            "name": "Hole",
        },
    )
    after = (await s.call("get_part_properties", base))["parts"][0]["volume"]
    flipped = False
    if cut["status"] != "OK" or close(after, before, 1.0):
        await s.call("delete_feature", {**base, "feature_id": cut["feature_id"]})
        cut = await s.call(
            "extrude",
            {
                **base,
                "sketch_feature_id": face_sketch["feature_id"],
                "depth": 10,
                "operation": "remove",
                "opposite_direction": True,
                "name": "Hole",
            },
        )
        after = (await s.call("get_part_properties", base))["parts"][0]["volume"]
        flipped = True
    expected = math.pi * 4**2 * 10
    check(
        close(before - after, expected, expected * 0.02),
        "hole removed ~pi*4^2*10 mm^3",
        f"removed {before - after:.1f} mm^3, expected {expected:.1f}; "
        f"needed opposite_direction={flipped}",
    )

    print("\n7. Editing")
    plate = (await s.call("list_features", base))["features"]
    plate_extrude = next(f for f in plate if f["name"] == "Plate")
    up = await s.call(
        "update_feature",
        {**base, "feature_id": plate_extrude["feature_id"], "parameters": {"depth": 12}},
    )
    check(up["status"] == "OK", "update_feature accepted", str(up["status"]))
    z = (await s.call("get_part_properties", base))["bounding_box"]["size"][2]
    check(close(z, 12), "plate is now 12 mm tall", f"z size {z}")
    statuses = [f["status"] for f in (await s.call("list_features", base))["features"]]
    check(all(st == "OK" for st in statuses), "every feature reports OK", str(statuses))

    print("\n8. Screenshot")
    shot = await s.client.call_tool("capture_screenshot", {**base, "review": False})
    text = " ".join(c.text for c in shot.content if isinstance(c, TextContent))
    check(not any(isinstance(c, ImageContent) for c in shot.content), "review=false sends no image")
    import json

    meta = json.loads(text)
    path = meta["images"][0]["path"]
    check(bool(path) and Path(path).read_bytes()[:4] == b"\x89PNG", "PNG saved to disk", str(path))
    shot_on = await s.client.call_tool(
        "capture_screenshot", {**base, "review": True, "views": ["top"]}
    )
    check(
        any(isinstance(c, ImageContent) for c in shot_on.content), "review=true returns the image"
    )

    print("\n9. Export")
    out = await s.call("export_model", {**base, "format": "STEP", "filename": "smoke"})
    head = Path(out["path"]).read_bytes()[:20]
    check(b"ISO-10303" in head, "STEP file written", out["path"])
    stl = await s.call("export_model", {**base, "format": "STL", "filename": "smoke"})
    check(Path(stl["path"]).stat().st_size > 84, "STL file written", stl["path"])

    print("\n10. Everything else (listing, FeatureScript, assembly, raw API)")
    docs = await s.call("list_documents", {"limit": 5})
    check(isinstance(docs["documents"], list), "list_documents", f"{len(docs['documents'])} found")
    got = await s.call("get_document", {"document_id": did})
    types = {e["type"] for e in got["elements"]}
    check("PARTSTUDIO" in types, "get_document lists elements", str(sorted(types)))
    parts = await s.call("list_parts", base)
    check(len(parts["parts"]) == 1, "list_parts finds the plate", str(parts["parts"]))
    fsr = await s.call(
        "run_featurescript",
        {
            **base,
            "script": "function(context is Context, queries) { return 6 * 7; }",
        },
    )
    check(fsr["result"] == 42, "run_featurescript returns a plain number", str(fsr["result"]))
    asm = await s.call("create_element", {"document_id": did, "name": "Asm", "kind": "assembly"})
    aid = asm["element_id"]
    check(bool(aid), "create_element (assembly)", aid)
    info = await s.call("get_assembly", {"document_id": did, "element_id": aid})
    check(
        info["instances"] == [] and info["length_unit"] == "mm",
        "get_assembly parses an empty assembly",
        str(info),
    )
    raw = await s.call("onshape_api_request", {"method": "GET", "path": f"/documents/{did}"})
    check(raw["response"].get("id") == did, "onshape_api_request GET", "")


async def custom_features(s: Session, base: dict, studio_url: str | None) -> None:
    print("\n11. Custom features (Feature Scripts)")
    own = await s.call("list_custom_features", base)
    check(
        isinstance(own["custom_features"], list),
        "list_custom_features runs on a Part Studio",
        f"{len(own['custom_features'])} custom feature(s) known to it",
    )
    if not studio_url:
        print("  (pass --custom-feature-studio <URL> to also add a real custom feature)")
        return
    listed = await s.call("list_custom_features", {**base, "feature_studio_url": studio_url})
    found = listed["custom_features"]
    check(bool(found), "the Feature Studio defines at least one custom feature", str(len(found)))
    if not found:
        return
    first = found[0]
    print(f"  using {first['feature_type']!r}: parameters {[p['id'] for p in first['parameters']]}")
    added = await s.call(
        "add_custom_feature",
        {**base, "feature_type": first["feature_type"], "feature_studio_url": studio_url},
    )
    # A feature that needs a selection (a face, an edge) reports ERROR without one, so accepting
    # the request is what is checked here; read the status below and look at the document.
    check(bool(added.get("feature_id")), "add_custom_feature accepted by Onshape", str(added))
    listed_after = await s.call("list_features", base)
    ids = [f["feature_id"] for f in listed_after["features"]]
    check(added.get("feature_id") in ids, "the custom feature is in the feature list", str(ids))


def main() -> None:
    parser = argparse.ArgumentParser(
        description=__doc__, formatter_class=argparse.RawTextHelpFormatter
    )
    parser.add_argument("--keep", action="store_true", help="do not trash the test document")
    parser.add_argument(
        "--public",
        action="store_true",
        help="create the test document as PUBLIC (required on Onshape free plans)",
    )
    parser.add_argument(
        "--custom-feature-studio",
        metavar="URL",
        help="URL of a Feature Studio tab: also add one of its custom features to the test doc",
    )
    parser.add_argument("--env-file", default=".env", help="optional KEY=VALUE file (default .env)")
    args = parser.parse_args()
    load_env_file(Path(args.env_file))
    ok = asyncio.run(run(args.keep, args.public, args.custom_feature_studio))
    sys.exit(0 if ok else 1)


if __name__ == "__main__":
    main()
