"""End-to-end tests: MCP Client -> server -> tools -> fake Onshape."""

import json

import pytest
from mcp import Client
from mcp_types import ImageContent, TextContent

from fake_onshape import AID, DID, EID, WID

BASE = {"document_id": DID, "element_id": EID}


async def call(server, name, args=None, *, expect_error=False):
    async with Client(server) as client:
        result = await client.call_tool(name, args or {})
    assert bool(result.is_error) == expect_error, result
    return result


def text_of(result) -> str:
    return "\n".join(c.text for c in result.content if isinstance(c, TextContent))


def images_of(result):
    return [c for c in result.content if isinstance(c, ImageContent)]


class TestRegistration:
    async def test_tool_catalog(self, make_server):
        async with Client(make_server()) as client:
            tools = {t.name: t for t in (await client.list_tools()).tools}
        assert len(tools) == 22
        assert tools["get_settings"].annotations.read_only_hint is True
        assert tools["delete_feature"].annotations.destructive_hint is True
        # every tool must explain itself to the model
        for tool in tools.values():
            assert tool.description and len(tool.description) > 30, tool.name

    async def test_read_only_mode_hides_every_write_tool(self, make_server):
        async with Client(make_server(ONSHAPE_READ_ONLY="true")) as client:
            names = {t.name for t in (await client.list_tools()).tools}
        assert not names & {
            "create_document",
            "create_element",
            "create_sketch",
            "extrude",
            "fillet",
            "chamfer",
            "update_feature",
            "delete_feature",
            "add_feature",
        }
        assert {"list_documents", "capture_screenshot", "onshape_api_request"} <= names

    async def test_server_starts_and_lists_tools_without_credentials(self, make_server):
        async with Client(make_server(ONSHAPE_ACCESS_KEY="", ONSHAPE_SECRET_KEY="")) as client:
            assert (await client.list_tools()).tools


class TestSettingsAndErrors:
    async def test_get_settings_reports_units_and_hides_keys(self, make_server):
        result = await call(make_server(ONSHAPE_UNITS="imperial"), "get_settings")
        data = result.structured_content
        assert data["units"] == "imperial" and data["length_unit"] == "in"
        assert "test-secret" not in json.dumps(data)

    async def test_missing_credentials_message_reaches_the_model(self, make_server):
        server = make_server(ONSHAPE_ACCESS_KEY="", ONSHAPE_SECRET_KEY="")
        result = await call(server, "whoami", expect_error=True)
        assert "ONSHAPE_ACCESS_KEY" in text_of(result)

    async def test_api_error_message_reaches_the_model(self, make_server, fake):
        fake.fail_next = (404, {"message": "Document not found"})
        result = await call(make_server(), "get_document", {"document_id": DID}, expect_error=True)
        message = text_of(result)
        assert "404" in message and "Document not found" in message

    async def test_whoami_rejects_an_anonymous_answer(self, make_server, fake):
        """Real Onshape replies 200 with {} when it does not recognise the keys."""
        fake.anonymous_session = True
        result = await call(make_server(), "whoami", expect_error=True)
        message = text_of(result)
        assert "anonymous" in message and "48" in message

    async def test_whoami(self, make_server):
        data = (await call(make_server(), "whoami")).structured_content
        assert data["connected"] and data["name"] == "Test User"


class TestDocuments:
    async def test_list_and_get(self, make_server):
        server = make_server()
        listing = (await call(server, "list_documents", {"query": "brack"})).structured_content
        assert listing["documents"][0]["document_id"] == DID
        doc = (await call(server, "get_document", {"document_id": DID})).structured_content
        assert doc["workspace_id"] == WID
        assert {e["type"] for e in doc["elements"]} == {"PARTSTUDIO", "ASSEMBLY"}

    async def test_free_plan_error_tells_the_model_to_ask_before_going_public(
        self, make_server, fake
    ):
        fake.fail_next = (409, {"message": "Free accounts only allow access to public documents."})
        result = await call(make_server(), "create_document", {"name": "New"}, expect_error=True)
        message = text_of(result)
        assert "public=true" in message and "Nothing was created" in message
        assert "free plan" in message.lower()

    async def test_public_flag_is_sent_only_when_requested(self, make_server, fake):
        server = make_server()
        await call(server, "create_document", {"name": "A", "public": True})
        await call(server, "create_document", {"name": "B"})
        sent = [json.loads(r.content)["isPublic"] for r in fake.calls("POST", r"/documents$")]
        assert sent == [True, False]

    async def test_create_document_and_element(self, make_server, fake):
        server = make_server()
        doc = (await call(server, "create_document", {"name": "New"})).structured_content
        assert doc["document_id"] == DID and doc["workspace_id"] == WID
        sent = json.loads(fake.calls("POST", r"/documents$")[0].content)
        assert sent["isPublic"] is False
        element = (
            await call(server, "create_element", {"document_id": DID, "name": "PS"})
        ).structured_content
        assert element["kind"] == "part_studio" and element["element_id"]


class TestModelingFlow:
    async def test_sketch_extrude_roundtrip_metric(self, make_server, fake):
        server = make_server()
        sk = (
            await call(
                server,
                "create_sketch",
                {
                    **BASE,
                    "plane": "Front",
                    "shapes": [{"type": "rectangle", "corner1": [0, 0], "corner2": [40, 20]}],
                },
            )
        ).structured_content
        assert sk["status"] == "OK" and sk["feature_id"] == "F001"
        # plane ID was looked up via FeatureScript, not hard-coded
        assert fake.calls("POST", "featurescript")
        sent = json.loads(fake.calls("POST", "/features$")[0].content)["feature"]
        assert sent["parameters"][0]["queries"][0]["deterministicIds"] == ["JCC"]  # Front
        assert max(e["endParam"] for e in sent["entities"]) == pytest.approx(0.04)  # 40 mm in m

        ex = (
            await call(
                server,
                "extrude",
                {**BASE, "sketch_feature_id": sk["feature_id"], "depth": 10, "operation": "new"},
            )
        ).structured_content
        assert ex["feature_id"] == "F002"
        extrude_call = json.loads(fake.calls("POST", "/features$")[1].content)["feature"]
        depth = next(p for p in extrude_call["parameters"] if p["parameterId"] == "depth")
        assert depth["expression"] == "10 mm"

        listed = (await call(server, "list_features", BASE)).structured_content
        assert [f["feature_type"] for f in listed["features"]] == ["newSketch", "extrude"]
        assert all(f["status"] == "OK" for f in listed["features"])

    async def test_imperial_setting_changes_expressions(self, make_server, fake):
        server = make_server(ONSHAPE_UNITS="imperial")
        await call(server, "extrude", {**BASE, "sketch_feature_id": "S1", "depth": 2})
        feature = json.loads(fake.calls("POST", "/features$")[0].content)["feature"]
        depth = next(p for p in feature["parameters"] if p["parameterId"] == "depth")
        assert depth["expression"] == "2 in"

    async def test_plane_id_is_cached_per_session(self, make_server, fake):
        server = make_server()
        shape = [{"type": "circle", "center": [0, 0], "radius": 5}]
        for _ in range(2):
            await call(server, "create_sketch", {**BASE, "shapes": shape})
        assert len(fake.calls("POST", "featurescript")) == 1

    async def test_sketch_on_face_uses_the_given_id(self, make_server, fake):
        shape = [{"type": "circle", "center": [0, 0], "radius": 5}]
        await call(make_server(), "create_sketch", {**BASE, "plane": "JHF", "shapes": shape})
        assert not fake.calls("POST", "featurescript")
        sent = json.loads(fake.calls("POST", "/features$")[0].content)["feature"]
        assert sent["parameters"][0]["queries"][0]["deterministicIds"] == ["JHF"]

    async def test_bad_shape_gives_a_readable_error(self, make_server):
        result = await call(
            make_server(),
            "create_sketch",
            {**BASE, "shapes": [{"type": "rectangle", "corner1": [0, 0], "corner2": [0, 5]}]},
            expect_error=True,
        )
        assert "zero width or height" in text_of(result)

    async def test_error_status_produces_warning(self, make_server, fake):
        server = make_server()
        await call(server, "extrude", {**BASE, "sketch_feature_id": "S1", "depth": 5})
        fake.states["F001"] = "ERROR"
        # next add returns OK; check list_features exposes the ERROR state
        listed = (await call(server, "list_features", BASE)).structured_content
        assert listed["features"][0]["status"] == "ERROR"

    async def test_update_feature_edits_depth_in_configured_units(self, make_server, fake):
        server = make_server()
        await call(server, "extrude", {**BASE, "sketch_feature_id": "S1", "depth": 5})
        out = (
            await call(
                server,
                "update_feature",
                {**BASE, "feature_id": "F001", "parameters": {"depth": 25}},
            )
        ).structured_content
        assert out["status"] == "OK"
        stored = fake.features[0]
        depth = next(p for p in stored["parameters"] if p["parameterId"] == "depth")
        assert depth["expression"] == "25 mm"
        flag = {**BASE, "feature_id": "F001", "parameters": {"oppositeDirection": True}}
        await call(server, "update_feature", flag)
        opposite = next(
            p for p in fake.features[0]["parameters"] if p["parameterId"] == "oppositeDirection"
        )
        assert opposite["value"] is True

    async def test_update_feature_angle_numbers_are_degrees(self, make_server, fake):
        fake.features.append(
            {
                "featureId": "FR",
                "featureType": "revolve",
                "name": "R",
                "parameters": [
                    {
                        "btType": "BTMParameterQuantity-147",
                        "parameterId": "angle",
                        "expression": "90 deg",
                    }
                ],
            }
        )
        await call(
            make_server(),
            "update_feature",
            {**BASE, "feature_id": "FR", "parameters": {"angle": 45}},
        )
        assert fake.features[0]["parameters"][0]["expression"] == "45 deg"

    async def test_update_feature_rejects_unknown_parameter_and_feature(self, make_server):
        server = make_server()
        await call(server, "extrude", {**BASE, "sketch_feature_id": "S1", "depth": 5})
        bad_param = await call(
            server,
            "update_feature",
            {**BASE, "feature_id": "F001", "parameters": {"nope": 1}},
            expect_error=True,
        )
        assert "Available" in text_of(bad_param)
        bad_feature = await call(
            server,
            "update_feature",
            {**BASE, "feature_id": "ZZZ", "parameters": {"depth": 1}},
            expect_error=True,
        )
        assert "No feature" in text_of(bad_feature)

    async def test_delete_feature(self, make_server, fake):
        server = make_server()
        await call(server, "extrude", {**BASE, "sketch_feature_id": "S1", "depth": 5})
        await call(server, "delete_feature", {**BASE, "feature_id": "F001"})
        assert fake.features == []

    async def test_add_feature_wraps_raw_json(self, make_server, fake):
        raw = {"btType": "BTMFeature-134", "featureType": "cube", "name": "Cube", "parameters": []}
        out = (
            await call(make_server(), "add_feature", {**BASE, "feature": raw})
        ).structured_content
        assert out["feature_type"] == "cube"


class TestInspection:
    async def test_part_properties_are_converted_to_millimeters(self, make_server):
        data = (await call(make_server(), "get_part_properties", BASE)).structured_content
        assert data["bounding_box"]["size"] == [40.0, 20.0, 10.0]
        part = data["parts"][0]
        assert part["volume"] == pytest.approx(8000.0)  # 8e-6 m^3 -> mm^3
        assert part["centroid"] == [20.0, 10.0, 5.0]
        assert part["mass_kg"] == 0.5

    async def test_part_properties_for_a_single_part_uses_the_part_endpoints(
        self, make_server, fake
    ):
        data = (
            await call(make_server(), "get_part_properties", {**BASE, "part_id": "JHD"})
        ).structured_content
        assert data["bounding_box"]["size"] == [40.0, 20.0, 10.0]
        assert fake.calls("GET", r"/parts/.*/partid/JHD/boundingboxes$")
        assert fake.calls("GET", r"/parts/.*/partid/JHD/massproperties$")

    async def test_part_properties_in_inches(self, make_server):
        data = (
            await call(make_server(ONSHAPE_UNITS="imperial"), "get_part_properties", BASE)
        ).structured_content
        assert data["length_unit"] == "in"
        assert data["bounding_box"]["size"][0] == pytest.approx(40 / 25.4, abs=1e-4)

    async def test_body_details(self, make_server):
        data = (await call(make_server(), "get_body_details", BASE)).structured_content
        body = data["bodies"][0]
        faces = {f["id"]: f for f in body["faces"]}
        assert faces["JHF"]["origin"] == [0.0, 0.0, 10.0]
        assert faces["JHF"]["outward_normal"] == [0.0, 0.0, 1.0]
        assert faces["JHG"]["outward_normal"] == [0.0, 0.0, -1.0]  # reversed face flips
        assert body["edges"][0]["start"] == [0.0, 0.0, 0.0]
        assert body["edges"][0]["end"] == [40.0, 0.0, 0.0]

    async def test_get_assembly_converts_positions_and_tolerates_missing_mate_data(
        self, make_server
    ):
        data = (
            await call(make_server(), "get_assembly", {"document_id": DID, "element_id": AID})
        ).structured_content
        assert data["instances"][0]["name"] == "Bracket <1>"
        assert data["placements"][0]["position"] == [100.0, 200.0, 300.0]  # metres -> mm
        assert [m["name"] for m in data["mates"]] == [None, "Fastened 1"]
        assert data["mates"][1]["suppressed"] is True

    async def test_list_parts_and_featurescript(self, make_server):
        server = make_server()
        parts = (await call(server, "list_parts", BASE)).structured_content
        assert parts["parts"][0]["part_id"] == "JHD"
        fs = await call(
            server, "run_featurescript", {**BASE, "script": "function(c, q) { return 42; }"}
        )
        assert fs.structured_content["result"] == 42


class TestScreenshots:
    async def test_review_on_returns_images_to_the_model(self, make_server, fake):
        result = await call(make_server(), "capture_screenshot", BASE)
        assert len(images_of(result)) == 1
        summary = json.loads(text_of(result))
        assert summary["sent_to_model"] is True
        assert summary["approx_image_tokens"] == 800 * 600 // 750
        assert fake.shaded_calls[0]["outputWidth"] == "800"
        assert fake.shaded_calls[0]["edges"] == "show"

    async def test_review_off_via_setting_returns_no_images_but_saves_file(self, make_server):
        result = await call(
            make_server(ONSHAPE_SCREENSHOT_REVIEW="false"), "capture_screenshot", BASE
        )
        assert images_of(result) == []
        summary = json.loads(text_of(result))
        assert summary["sent_to_model"] is False
        assert "approx_image_tokens" not in summary
        path = summary["images"][0]["path"]
        assert path.endswith(".png")
        from pathlib import Path

        assert Path(path).read_bytes().startswith(b"\x89PNG")

    async def test_per_call_override_beats_the_setting(self, make_server):
        off = make_server(ONSHAPE_SCREENSHOT_REVIEW="false")
        assert len(images_of(await call(off, "capture_screenshot", {**BASE, "review": True}))) == 1
        on = make_server(ONSHAPE_SCREENSHOT_REVIEW="true")
        assert images_of(await call(on, "capture_screenshot", {**BASE, "review": False})) == []

    async def test_multiple_views_and_custom_size(self, make_server, fake):
        args = {**BASE, "views": ["front", "isometric", "front"], "width": 320, "height": 240}
        result = await call(make_server(), "capture_screenshot", args)
        assert len(images_of(result)) == 2  # duplicate 'front' collapsed
        assert [c["outputWidth"] for c in fake.shaded_calls] == ["320", "320"]
        matrices = [c["viewMatrix"] for c in fake.shaded_calls]
        assert matrices[0] == "front" and matrices[1].startswith("0.612")

    async def test_assembly_screenshot_uses_assembly_endpoint(self, make_server, fake):
        result = await call(
            make_server(), "capture_screenshot", {"document_id": DID, "element_id": AID}
        )
        assert len(images_of(result)) == 1
        assert fake.calls("GET", "/assemblies/.*/shadedviews")

    async def test_size_and_view_validation(self, make_server):
        await call(make_server(), "capture_screenshot", {**BASE, "width": 10}, expect_error=True)
        await call(
            make_server(),
            "capture_screenshot",
            {**BASE, "views": ["upside-down"]},
            expect_error=True,
        )

    async def test_screenshot_still_works_when_disk_is_unwritable(self, make_server, tmp_path):
        blocker = tmp_path / "blocker"
        blocker.write_text("a file, not a directory")
        server = make_server(ONSHAPE_OUTPUT_DIR=str(blocker / "sub"))
        result = await call(server, "capture_screenshot", BASE)
        assert len(images_of(result)) == 1
        assert json.loads(text_of(result))["images"][0]["path"] is None


class TestExport:
    @pytest.fixture(autouse=True)
    def _fast_polling(self, monkeypatch):
        monkeypatch.setattr("onshape_jamcp.tools.output.EXPORT_POLL_SECONDS", 0)

    async def test_step_export_follows_translation_and_redirect(self, make_server, fake):
        out = (
            await call(
                make_server(), "export_model", {**BASE, "format": "STEP", "filename": "bracket"}
            )
        ).structured_content
        assert out["path"].endswith("bracket.step")
        from pathlib import Path

        assert Path(out["path"]).read_bytes() == b"ISO-10303-21;STEP-FILE"
        body = json.loads(fake.calls("POST", "/translations$")[0].content)
        assert body["formatName"] == "STEP" and body["storeInDocument"] is False

    async def test_stl_uses_synchronous_endpoint_with_configured_units(self, make_server, fake):
        out = (
            await call(
                make_server(ONSHAPE_UNITS="imperial"), "export_model", {**BASE, "format": "STL"}
            )
        ).structured_content
        assert out["path"].endswith(".stl") and out["bytes"] == len(b"solid-binary-stl")
        assert (
            fake.calls("GET", "/stl$")[0].url.params["units"] == "inch"
        )  # lowercase: see output.py
        assert not fake.calls("POST", "/translations$")

    async def test_filename_is_sanitized(self, make_server):
        out = (
            await call(make_server(), "export_model", {**BASE, "filename": "../../etc/passwd"})
        ).structured_content
        assert ".." not in out["path"].replace("\\", "/").split("/exports/")[1]


class TestRawApi:
    async def test_get_passthrough(self, make_server, fake):
        data = (
            await call(
                make_server(),
                "onshape_api_request",
                {"method": "GET", "path": "/users/sessioninfo"},
            )
        ).structured_content
        assert data["response"]["id"] == "user1"

    @pytest.mark.parametrize(
        "path",
        ["https://evil.example/x", "//evil.example/x", "/a/../b", "documents", "/a?x=1", "/a\\b"],
    )
    async def test_bad_paths_are_rejected(self, make_server, path, fake):
        result = await call(
            make_server(), "onshape_api_request", {"method": "GET", "path": path}, expect_error=True
        )
        assert "relative Onshape API path" in text_of(result)
        assert not fake.requests

    async def test_writes_blocked_in_read_only_mode(self, make_server, fake):
        server = make_server(ONSHAPE_READ_ONLY="true")
        result = await call(
            server,
            "onshape_api_request",
            {"method": "DELETE", "path": "/documents/x"},
            expect_error=True,
        )
        assert "read-only" in text_of(result)
        assert not fake.requests

    async def test_large_responses_are_truncated(self, make_server, monkeypatch):
        monkeypatch.setattr("onshape_jamcp.tools.raw.MAX_RESPONSE_CHARS", 50)
        data = (
            await call(
                make_server(), "onshape_api_request", {"method": "GET", "path": "/documents"}
            )
        ).structured_content
        assert data["truncated"] is True and len(data["preview"]) == 50
