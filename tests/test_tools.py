"""End-to-end tests: MCP Client -> server -> tools -> fake Onshape."""

import json
import re

import pytest
from mcp import Client
from mcp_types import ImageContent, TextContent

from fake_onshape import (
    AID,
    DID,
    EID,
    FSID,
    OTHER_DID,
    THREAD_NAMESPACE,
    THREAD_SPEC,
    VID,
    WID,
)

BASE = {"document_id": DID, "element_id": EID}
STUDIO_URL = f"https://cad.onshape.com/documents/{DID}/v/{VID}/e/{FSID}"


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
        assert len(tools) == 37
        assert tools["get_settings"].annotations.read_only_hint is True
        assert tools["list_custom_features"].annotations.read_only_hint is True
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
            "add_custom_feature",
            "set_variable",
            "set_configuration_input",
            "insert_instance",
            "add_mate",
            "rename",
            "circular_pattern",
            "linear_pattern",
            "mirror",
            "revolve",
            "shell",
            "boolean",
        }
        assert {
            "list_documents",
            "capture_screenshot",
            "onshape_api_request",
            "list_custom_features",
            "list_variables",
            "get_configuration",
        } <= names

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


class TestExtrudeNames:
    ARGS = {**BASE, "sketch_feature_id": "S1"}

    async def name_of(self, make_server, fake, **extra):
        await call(make_server(**extra.pop("env", {})), "extrude", {**self.ARGS, **extra})
        return fake.features[-1]["name"]

    async def test_names_carry_the_size_by_default(self, make_server, fake):
        assert await self.name_of(make_server, fake, depth=8) == "Extrude 8 mm"
        assert await self.name_of(make_server, fake, depth=2.5, operation="remove") == "Cut 2.5 mm"
        assert await self.name_of(make_server, fake, depth="12 mm") == "Extrude 12 mm"

    async def test_a_variable_depth_shows_the_variable(self, make_server, fake):
        # never "#head_h": Onshape reads #word in a name as a parameter of the feature -> "?"
        assert await self.name_of(make_server, fake, depth="#head_h") == "Extrude (head_h)"
        assert await self.name_of(make_server, fake, depth="#d / 2") == "Extrude (d / 2)"
        assert await self.name_of(make_server, fake, depth="#t", operation="remove") == "Cut (t)"

    async def test_units_follow_the_setting(self, make_server, fake):
        name = await self.name_of(make_server, fake, depth=0.5, env={"ONSHAPE_UNITS": "imperial"})
        assert name == "Extrude 0.5 in"

    async def test_symmetric_is_said(self, make_server, fake):
        assert await self.name_of(make_server, fake, depth=8, symmetric=True) == (
            "Extrude 8 mm (symmetric)"
        )

    async def test_holes_get_no_size(self, make_server, fake):
        name = await self.name_of(make_server, fake, depth=20, operation="remove", hole=True)
        assert name == "Hole"

    async def test_an_explicit_name_always_wins(self, make_server, fake):
        assert await self.name_of(make_server, fake, depth=8, name="Base plate") == "Base plate"

    async def test_the_name_follows_a_changed_depth(self, make_server, fake):
        server = make_server()
        await call(server, "extrude", {**self.ARGS, "depth": 5})
        await call(
            server, "update_feature", {**BASE, "feature_id": "F001", "parameters": {"depth": 25}}
        )
        assert fake.features[0]["name"] == "Extrude 25 mm"
        await call(server, "extrude", {**self.ARGS, "depth": 5, "name": "Base plate"})
        await call(
            server, "update_feature", {**BASE, "feature_id": "F002", "parameters": {"depth": 9}}
        )
        assert fake.features[1]["name"] == "Base plate"  # a name somebody chose is never touched
        await call(
            server, "extrude", {**self.ARGS, "depth": 5, "operation": "remove", "hole": True}
        )
        await call(
            server, "update_feature", {**BASE, "feature_id": "F003", "parameters": {"depth": 9}}
        )
        assert fake.features[2]["name"] == "Hole"
        await call(
            server,
            "update_feature",
            {**BASE, "feature_id": "F001", "parameters": {"depth": 30}, "name": "Mine"},
        )
        assert fake.features[0]["name"] == "Mine"

    async def test_a_variable_name_is_refreshed_and_a_legacy_hash_name_is_repaired(
        self, make_server, fake
    ):
        server = make_server()
        await call(server, "extrude", {**self.ARGS, "depth": "#head_h"})
        assert fake.features[0]["name"] == "Extrude (head_h)"
        await call(
            server, "update_feature", {**BASE, "feature_id": "F001", "parameters": {"depth": 12}}
        )
        assert fake.features[0]["name"] == "Extrude 12 mm"
        # an old "Extrude #head_h" (shown as "Extrude ?" by Onshape) is fixed on the next change
        fake.features[0]["name"] = "Extrude #head_h"
        await call(
            server, "update_feature", {**BASE, "feature_id": "F001", "parameters": {"depth": 7}}
        )
        assert fake.features[0]["name"] == "Extrude 7 mm"


class TestNamesNeverStartAFeatureReference:
    """In a feature name Onshape reads '#word' as a parameter of that feature (shown as '?')."""

    def test_shown_writes_variables_in_brackets(self):
        from onshape_jamcp import names

        assert names.shown("8 mm") == "8 mm"
        assert names.shown("#head_h") == "(head_h)"
        assert names.shown("2 * #d + #t") == "(2 * d + t)"

    def test_generated_names_contain_no_hash(self):
        from onshape_jamcp import names

        generated = [
            names.extrude_name("new", "#depth", symmetric=True),
            names.extrude_name("remove", "#cut"),
            names.circular_pattern_name("#spokes", "#sweep"),
            names.linear_pattern_name("#n", "#pitch", ("#rows", "#gap")),
            names.revolve_name("#turn"),
            names.shell_name("#wall"),
        ]
        assert all("#" not in n for n in generated), generated
        assert generated[0] == "Extrude (depth) (symmetric)"
        assert generated[3] == "Linear pattern x(n) @ (pitch), x(rows) @ (gap)"

    async def test_variables_are_named_after_themselves(self, make_server, fake):
        server = make_server()
        await call(server, "set_variable", {**BASE, "variables": {"d": 10, "pitch": 1.5}})
        assert [f["name"] for f in fake.features] == ["d", "pitch"]


class TestFewerRequests:
    async def extrudes(self, server, n=3):
        for _ in range(n):
            await call(server, "extrude", {**BASE, "sketch_feature_id": "S1", "depth": 5})

    async def test_several_features_change_in_one_update_request(self, make_server, fake):
        server = make_server()
        await self.extrudes(server)
        updates = [
            {"feature_id": "F001", "parameters": {"depth": 10}},
            {"feature_id": "F003", "parameters": {"depth": "#h", "oppositeDirection": True}},
        ]
        out = (
            await call(server, "update_feature", {**BASE, "updates": updates})
        ).structured_content
        assert [u["feature_id"] for u in out["updated"]] == ["F001", "F003"]
        assert all(u["status"] == "OK" for u in out["updated"]) and out["onshape_requests"] == 2
        assert len(fake.batch_updates) == 1  # ONE request for both
        assert len(fake.calls("POST", r"/features/featureid/")) == 0
        sent = fake.batch_updates[0]
        assert sent["btType"] == "BTUpdateFeaturesCall-1748"
        by_id = {f["featureId"]: f for f in sent["features"]}
        assert [p["parameterId"] for p in by_id["F001"]["parameters"]] == [
            "depth"
        ]  # only changed ones
        assert "name" not in by_id["F001"]
        depth = {p["parameterId"]: p for p in fake.features[2]["parameters"]}
        assert depth["depth"]["expression"] == "#h" and depth["oppositeDirection"]["value"] is True
        assert fake.features[1]["parameters"][2]["expression"] == "5 mm"  # F002 untouched

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            ({"updates": [{"feature_id": "NOPE", "parameters": {"depth": 1}}]}, "No feature"),
            (
                {
                    "updates": [
                        {"feature_id": "F001", "parameters": {"depth": 1}},
                        {"feature_id": "F001", "parameters": {"depth": 2}},
                    ]
                },
                "each feature once",
            ),
            ({"updates": [{"feature_id": "F001", "parameters": {"nope": 1}}]}, "Available"),
            (
                {
                    "updates": [{"feature_id": "F001", "parameters": {"depth": 1}}],
                    "feature_id": "F001",
                    "parameters": {"depth": 1},
                },
                "not both",
            ),
            ({}, "Give feature_id"),
        ],
    )
    async def test_bad_batch_updates_send_nothing(self, make_server, fake, args, message):
        server = make_server()
        await self.extrudes(server, 1)
        result = await call(server, "update_feature", {**BASE, **args}, expect_error=True)
        assert message in text_of(result) and not fake.batch_updates

    async def test_many_variables_in_one_call(self, make_server, fake):
        server = make_server()
        await call(server, "set_variable", {**BASE, "name": "d", "value": 10})
        reads_before = len(fake.calls("GET", "/features$"))
        out = (
            await call(
                server,
                "set_variable",
                {**BASE, "variables": {"d": 14, "pitch": "1.5 mm", "head_h": 6.4}},
            )
        ).structured_content
        assert [(v["variable"], v["created"]) for v in out["variables"]] == [
            ("d", False),
            ("pitch", True),
            ("head_h", True),
        ]
        assert [v["expression"] for v in out["variables"]] == ["14 mm", "1.5 mm", "6.4 mm"]
        assert len(fake.calls("GET", "/features$")) - reads_before == 1  # one read for all three
        assert (
            len(fake.batch_updates) == 1 and out["onshape_requests"] == 4
        )  # read + update + 2 new
        assert fake.features[0]["parameters"][3]["expression"] == "14 mm"

    async def test_variables_cannot_be_mixed_with_a_single_variable(self, make_server, fake):
        for args in (
            {"name": "d", "variables": {"x": 1}},
            {"value": 1, "variables": {"x": 1}},
            {"variables": {"bad name": 1}},
        ):
            await call(make_server(), "set_variable", {**BASE, **args}, expect_error=True)
        assert not fake.features

    async def test_sketch_variables_need_no_feature_list_read(self, make_server, fake):
        server = make_server()
        await call(server, "set_variable", {**BASE, "name": "d", "value": 12})
        reads = len(fake.calls("GET", "/features$"))
        shapes = [{"type": "circle", "center": [0, 0], "diameter": "#d"}]
        await call(server, "create_sketch", {**BASE, "plane": "JDC", "shapes": shapes})
        assert len(fake.calls("GET", "/features$")) == reads  # FeatureScript answered it alone

    async def test_the_big_spec_download_is_reused(self, make_server, fake):
        server = make_server()
        args = {**BASE, "feature_type": "threadCreator"}
        await call(server, "add_custom_feature", args)
        await call(server, "add_custom_feature", args)
        await call(server, "add_custom_feature", args)
        assert len(fake.calls("GET", "featurespecs")) == 1
        await call(server, "list_custom_features", BASE)  # an explicit listing is always fresh
        assert len(fake.calls("GET", "featurespecs")) == 2

    async def test_a_cached_miss_is_retried_once_with_fresh_specs(self, make_server, fake):
        server = make_server()
        fake.part_studio_specs = [fake.part_studio_specs[0]]  # nothing custom yet
        await call(
            server,
            "add_custom_feature",
            {**BASE, "feature_type": "threadCreator"},
            expect_error=True,
        )
        assert len(fake.calls("GET", "featurespecs")) == 2  # cached copy, then one fresh look
        fake.part_studio_specs.append(THREAD_SPEC)  # the script becomes available
        await call(server, "add_custom_feature", {**BASE, "feature_type": "threadCreator"})
        assert len(fake.calls("GET", "featurespecs")) == 3  # not found in the cache: refetched


class TestMates:
    ASM = {"document_id": DID, "assembly_id": AID}

    async def two_instances(self, server):
        await call(
            server,
            "insert_instance",
            {**self.ASM, "source_element_id": EID, "part_id": "JHD", "fixed": True},
        )
        await call(
            server, "insert_instance", {**self.ASM, "source_element_id": EID, "part_id": "JeD"}
        )
        # the fake already holds I1; the two inserted ones are I2 (bolt) and I3 (nut)
        return "I2", "I3"

    async def test_a_mate_embeds_two_implicit_connectors_the_way_onshape_does(
        self, make_server, fake
    ):
        server = make_server()
        bolt, nut = await self.two_instances(server)
        out = (
            await call(
                server,
                "add_mate",
                {
                    **self.ASM,
                    "mate_type": "cylindrical",
                    "first": {"instance_id": bolt, "entity_id": "JMF", "at": "center"},
                    "second": {"instance_id": nut, "entity_id": "J6G", "at": "centroid"},
                },
            )
        ).structured_content
        assert out["status"] == "OK" and out["name"] == "Cylindrical 1"
        mate = fake.asm_features[0]
        assert mate["btType"] == "BTMMate-64" and mate["featureType"] == "mate"
        connectors = mate["mateConnectors"]
        assert [c["btType"] for c in connectors] == ["BTMMateConnector-66"] * 2
        assert all(c["implicit"] and c["isHidden"] for c in connectors)
        queries = [
            next(p for p in c["parameters"] if p["parameterId"] == "originQuery")["queries"][0]
            for c in connectors
        ]
        assert [(q["inferenceType"], q["deterministicIds"], q["path"]) for q in queries] == [
            ("CENTER", ["JMF"], [bolt]),
            ("CENTROID", ["J6G"], [nut]),
        ]
        by_id = {p["parameterId"]: p for p in mate["parameters"]}
        assert by_id["mateType"]["value"] == "CYLINDRICAL"
        refs = by_id["mateConnectorsQuery"]["queries"]
        assert [r["btType"] for r in refs] == ["BTMFeatureQueryWithOccurrence-157"] * 2
        # the mate refers to its own connectors by feature id (not to geometry directly)
        assert [r["featureId"] for r in refs] == [c["featureId"] for c in connectors]
        assert len({c["featureId"] for c in connectors}) == 2
        assert "primaryAxisAlignment" not in by_id and "mateOffsetEnabled" not in by_id

    async def test_origin_flip_and_offset(self, make_server, fake):
        server = make_server()
        bolt, nut = await self.two_instances(server)
        await call(
            server,
            "add_mate",
            {
                **self.ASM,
                "mate_type": "fastened",
                "first": {"instance_id": bolt, "at": "origin"},
                "second": {"instance_id": nut, "entity_id": "J6G", "at": "centroid"},
                "flip": True,
                "offset": [0, 0, 5],
            },
        )
        mate = fake.asm_features[0]
        first = mate["mateConnectors"][0]["parameters"][1]["queries"][0]
        assert first["inferenceType"] == "PART_ORIGIN" and first["deterministicIds"] == []
        by_id = {p["parameterId"]: p for p in mate["parameters"]}
        assert by_id["primaryAxisAlignment"]["value"] is False
        assert by_id["mateOffsetEnabled"]["value"] is True
        assert by_id["mateOffsetDistanceZ"]["expression"] == "5 mm"
        assert by_id["mateOffsetDistanceX"]["expression"] == "0 mm"

    async def test_names_count_up_per_type(self, make_server, fake):
        server = make_server()
        bolt, nut = await self.two_instances(server)

        def side(instance):
            return {"instance_id": instance, "entity_id": "X", "at": "centroid"}

        args = {**self.ASM, "first": side(bolt), "second": side(nut)}
        await call(server, "add_mate", {**args, "mate_type": "revolute"})
        # the fake assembly already holds a "Fastened 1", so the next fastened mate is number 2
        await call(server, "add_mate", {**args, "mate_type": "fastened"})
        await call(server, "add_mate", {**args, "mate_type": "pin_slot", "name": "Slot"})
        assert [f["name"] for f in fake.asm_features] == ["Revolute 1", "Fastened 2", "Slot"]

    async def test_an_error_status_is_reported_not_hidden(self, make_server, fake):
        server = make_server()
        bolt, nut = await self.two_instances(server)
        fake.mate_status = "ERROR"
        out = (
            await call(
                server,
                "add_mate",
                {
                    **self.ASM,
                    "mate_type": "fastened",
                    "first": {"instance_id": bolt, "entity_id": "JMK", "at": "center"},
                    "second": {"instance_id": nut, "entity_id": "J6G", "at": "centroid"},
                },
            )
        ).structured_content
        assert out["status"] == "ERROR" and "circular edge" in out["warning"]
        assert [p["instance_id"] for p in out["positions"]] == [bolt, nut]

    @pytest.mark.parametrize(
        ("first", "second", "message"),
        [
            (
                {"instance_id": "NOPE", "entity_id": "A"},
                {"instance_id": "I3", "entity_id": "B"},
                "No instance",
            ),
            (
                {"instance_id": "I2", "entity_id": "A"},
                {"instance_id": "I2", "entity_id": "B"},
                "two different",
            ),
            ({"instance_id": "I2"}, {"instance_id": "I3", "entity_id": "B"}, "needs an entity_id"),
        ],
    )
    async def test_bad_mates_send_nothing(self, make_server, fake, first, second, message):
        server = make_server()
        await self.two_instances(server)
        result = await call(
            server,
            "add_mate",
            {**self.ASM, "mate_type": "fastened", "first": first, "second": second},
            expect_error=True,
        )
        assert message in text_of(result) and not fake.asm_features


class TestAssembly:
    ASM = {"document_id": DID, "assembly_id": AID, "source_element_id": EID}

    async def test_insert_one_part_reports_the_new_instance(self, make_server, fake):
        out = (
            await call(
                make_server(), "insert_instance", {**self.ASM, "part_id": "JeD", "fixed": True}
            )
        ).structured_content
        assert out["instances"] == [{"instance_id": "I2", "name": "JeD <2>"}]
        assert out["fixed"] is True
        sent = fake.inserted[0]
        assert sent == {
            "documentId": DID,
            "elementId": EID,
            "isAssembly": False,
            "isWholePartStudio": False,
            "isFixed": True,
            "partId": "JeD",
        }

    async def test_insert_a_whole_part_studio_in_a_configuration(self, make_server, fake):
        args = {**self.ASM, "configuration": {"Size": "M18"}}
        out = (await call(make_server(), "insert_instance", args)).structured_content
        assert out["configuration"] == "Size=M18" and len(out["instances"]) == 1
        sent = fake.inserted[0]
        assert sent["isWholePartStudio"] is True and "partId" not in sent
        assert sent["configuration"] == "Size=M18" and sent["isFixed"] is False

    async def test_insert_from_another_document(self, make_server, fake):
        args = {**self.ASM, "source_document_id": "f" * 24, "part_id": "JHD"}
        await call(make_server(), "insert_instance", args)
        assert fake.inserted[0]["documentId"] == "f" * 24

    async def test_get_assembly_sees_inserted_instances(self, make_server):
        server = make_server()
        await call(server, "insert_instance", {**self.ASM, "part_id": "JeD"})
        data = (
            await call(server, "get_assembly", {"document_id": DID, "element_id": AID})
        ).structured_content
        assert [i["instance_id"] for i in data["instances"]] == ["I1", "I2"]


class TestConfigurations:
    SIZE = {**BASE, "name": "Size", "kind": "list", "options": ["M10", "M18"]}

    async def test_list_input_is_created_and_listed(self, make_server, fake):
        server = make_server()
        out = (await call(server, "set_configuration_input", self.SIZE)).structured_content
        assert out["inputs"] == [
            {"name": "Size", "type": "list", "options": ["M10", "M18"], "default": "M10"}
        ]
        stored = fake.config_params[0]
        assert stored["btType"] == "BTMConfigurationParameterEnum-105"
        assert stored["enumName"] == "Size_conf" and stored["parameterType"] == "ENUM"
        assert [o["option"] for o in stored["options"]] == ["M10", "M18"]
        listed = (await call(server, "get_configuration", BASE)).structured_content
        assert listed == out

    async def test_inputs_are_merged_by_name(self, make_server, fake):
        server = make_server()
        await call(server, "set_configuration_input", self.SIZE)
        await call(
            server, "set_configuration_input", {**self.SIZE, "options": ["M8", "M10", "M18"]}
        )
        await call(
            server,
            "set_configuration_input",
            {
                **BASE,
                "name": "Length",
                "kind": "length",
                "default": 40,
                "minimum": 10,
                "maximum": 200,
            },
        )
        names = [p["parameterId"] for p in fake.config_params]
        assert names == ["Size", "Length"]
        assert len(fake.config_params[0]["options"]) == 3
        length = fake.config_params[1]["rangeAndDefault"]
        assert (length["minValue"], length["defaultValue"], length["maxValue"]) == (0.01, 0.04, 0.2)
        assert length["units"] == "meter"

    @pytest.mark.parametrize(
        "args",
        [
            {"kind": "list"},
            {"kind": "list", "options": ["only"]},
            {"kind": "list", "options": ["a", "b"], "default": "c"},
            {"kind": "length", "default": 5},
        ],
    )
    async def test_bad_inputs_are_refused(self, make_server, fake, args):
        result = await call(
            make_server(),
            "set_configuration_input",
            {**BASE, "name": "X", **args},
            expect_error=True,
        )
        assert result.is_error and not fake.calls("POST", "/configuration")

    async def test_a_variable_can_depend_on_a_list_input(self, make_server, fake):
        server = make_server()
        await call(server, "set_configuration_input", self.SIZE)
        out = (
            await call(
                server,
                "set_variable",
                {**BASE, "name": "d", "by_option": {"Size": {"M10": 10, "M18": 18}}},
            )
        ).structured_content
        assert out["status"] == "OK" and "M18=18 mm" in out["expression"]
        value = next(p for p in fake.features[0]["parameters"] if p["parameterId"] == "lengthValue")
        assert value["btType"] == "BTMParameterConfigured-2222"
        assert value["configurationParameterId"] == "Size"
        inner = {v["enumValue"]: v for v in value["values"]}
        assert inner["M10"]["value"]["expression"] == "10 mm"
        assert inner["M18"]["value"]["expression"] == "18 mm"
        assert "parameterId" not in inner["M18"]["value"]  # Onshape rejects labeled inner values
        assert inner["M18"]["enumName"] == "Size_conf" and inner["M18"]["btType"].endswith("1923")
        listed = (await call(server, "list_variables", BASE)).structured_content["variables"]
        assert listed[0]["configured_by"] == "Size"
        assert listed[0]["by_option"] == {"M10": "10 mm", "M18": "18 mm"}

    @pytest.mark.parametrize(
        ("by_option", "message"),
        [
            ({"Size": {"M10": 10}}, "missing: \\['M18'\\]"),
            ({"Size": {"M10": 10, "M18": 18, "M24": 24}}, "unknown: \\['M24'\\]"),
            ({"Nope": {"a": 1, "b": 2}}, "set_configuration_input"),
        ],
    )
    async def test_by_option_must_match_the_input(self, make_server, fake, by_option, message):
        server = make_server()
        await call(server, "set_configuration_input", self.SIZE)
        result = await call(
            server,
            "set_variable",
            {**BASE, "name": "d", "by_option": by_option},
            expect_error=True,
        )
        assert re.search(message, text_of(result))
        assert not fake.features

    async def test_value_and_by_option_are_exclusive(self, make_server):
        await call(
            make_server(),
            "set_variable",
            {**BASE, "name": "d", "value": 5, "by_option": {"Size": {"M10": 1, "M18": 2}}},
            expect_error=True,
        )
        await call(make_server(), "set_variable", {**BASE, "name": "d"}, expect_error=True)

    async def test_configuration_reaches_measure_and_view_calls(self, make_server, fake):
        server = make_server()
        cfg = {"configuration": {"Size": "M18", "Length": 40}}
        await call(server, "get_part_properties", {**BASE, **cfg, "tight": False})
        await call(server, "get_body_details", {**BASE, **cfg})
        await call(server, "capture_screenshot", {**BASE, **cfg})
        for pattern in ("boundingboxes", "massproperties", "bodydetails", "shadedviews"):
            sent = fake.calls("GET", pattern)[0].url.params["configuration"]
            assert sent == "Size=M18;Length=40 mm", pattern
        await call(server, "get_part_properties", BASE)
        assert "configuration" not in fake.calls("GET", "boundingboxes")[-1].url.params

    async def test_bad_configuration_entries_are_refused(self, make_server, fake):
        result = await call(
            make_server(),
            "get_part_properties",
            {**BASE, "configuration": {"Size": "M18;Length=1"}},
            expect_error=True,
        )
        assert result.is_error and not fake.requests


def feature_params(feature):
    return {p["parameterId"]: p for p in feature["parameters"]}


def ids_of(parameter):
    return parameter["queries"][0]["deterministicIds"]


class TestPatterns:
    @staticmethod
    def grow(fake, volume=12e-6):
        fake.after_add = lambda feature: fake.volumes.update({"JHD": volume})

    async def test_circular_pattern_of_a_feature(self, make_server, fake):
        self.grow(fake)
        out = (
            await call(
                make_server(),
                "circular_pattern",
                {**BASE, "count": 6, "axis": "JXA", "feature_ids": ["F010"]},
            )
        ).structured_content
        assert out["status"] == "OK" and out["name"] == "Circular pattern x6"
        feature = fake.features[0]
        p = feature_params(feature)
        assert feature["featureType"] == "circularPattern"
        assert (p["patternType"]["value"], p["patternType"]["enumName"]) == (
            "FEATURE",
            "PatternType",
        )
        assert p["instanceFunction"]["btType"] == "BTMParameterFeatureList-1749"
        assert p["instanceFunction"]["featureIds"] == ["F010"]
        assert (
            p["fullFeaturePattern"]["value"] is True
        )  # the "faster" mode fails on merged features
        assert ids_of(p["axis"]) == ["JXA"]
        assert (p["instanceCount"]["expression"], p["instanceCount"]["isInteger"]) == ("6", True)
        assert p["angle"]["expression"] == "360 deg" and p["equalSpace"]["value"] is True
        assert "operationType" not in p and "entities" not in p
        effect = out["effect"]
        assert effect["parts_before"] == effect["parts_after"] == 1
        assert effect["volume_before"] == pytest.approx(8000) and effect[
            "volume_after"
        ] == pytest.approx(12000)
        assert "warning" not in out

    async def test_a_pattern_that_changes_nothing_is_flagged(self, make_server, fake):
        out = (
            await call(
                make_server(),
                "circular_pattern",
                {**BASE, "count": 3, "axis": "JXA", "part_ids": ["JHD"]},
            )
        ).structured_content
        assert out["status"] == "OK" and "did not change" in out["warning"]

    async def test_a_variable_count_and_a_partial_angle(self, make_server, fake):
        self.grow(fake)
        out = (
            await call(
                make_server(),
                "circular_pattern",
                {**BASE, "count": "#spokes", "axis": "J1", "feature_ids": ["F1"], "angle": 90},
            )
        ).structured_content
        p = feature_params(fake.features[0])
        assert p["instanceCount"]["expression"] == "#spokes"
        assert p["angle"]["expression"] == "90 deg"
        assert out["name"] == "Circular pattern x(spokes) over 90 deg"

    async def test_the_axis_can_be_the_construction_line_of_a_sketch(self, make_server, fake):
        self.grow(fake)
        await call(
            make_server(),
            "circular_pattern",
            {**BASE, "count": 4, "axis": "sketch:F001", "feature_ids": ["F1"]},
        )
        assert ids_of(feature_params(fake.features[0])["axis"]) == ["JGC"]
        script = json.loads(fake.calls("POST", "featurescript")[-1].content)["script"]
        assert 'makeId("F001")' in script and "ConstructionObject.YES" in script

    @pytest.mark.parametrize(("lines", "found"), [([], "0"), (["JA", "JB"], "2")])
    async def test_a_sketch_needs_exactly_one_construction_line(
        self, make_server, fake, lines, found
    ):
        fake.construction_edges = lines
        result = await call(
            make_server(),
            "circular_pattern",
            {**BASE, "count": 4, "axis": "sketch:F001", "feature_ids": ["F1"]},
            expect_error=True,
        )
        assert f"has {found} construction lines" in text_of(result) and not fake.features

    async def test_parts_are_patterned_into_new_parts_or_merged_into_scope(self, make_server, fake):
        server = make_server()
        base = {**BASE, "count": 3, "axis": "J1", "part_ids": ["JHD"]}
        await call(server, "circular_pattern", base)
        p = feature_params(fake.features[0])
        assert p["patternType"]["value"] == "PART" and p["operationType"]["value"] == "NEW"
        assert ids_of(p["entities"]) == ["JHD"] and "booleanScope" not in p
        await call(server, "circular_pattern", {**base, "operation": "add", "merge_with": ["JP1"]})
        p = feature_params(fake.features[1])
        assert p["operationType"]["value"] == "ADD"
        assert p["defaultScope"]["value"] is False and ids_of(p["booleanScope"]) == ["JP1"]

    async def test_faces_can_be_patterned(self, make_server, fake):
        await call(
            make_server(),
            "circular_pattern",
            {**BASE, "count": 3, "axis": "J1", "face_ids": ["JHF"]},
        )
        p = feature_params(fake.features[0])
        assert p["patternType"]["value"] == "FACE" and ids_of(p["faces"]) == ["JHF"]

    @pytest.mark.parametrize(
        "selection",
        [{}, {"part_ids": ["A"], "feature_ids": ["F1"]}, {"part_ids": [], "face_ids": []}],
    )
    async def test_exactly_one_thing_to_repeat(self, make_server, fake, selection):
        result = await call(
            make_server(),
            "circular_pattern",
            {**BASE, "count": 3, "axis": "J1", **selection},
            expect_error=True,
        )
        assert "exactly one of part_ids" in text_of(result) and not fake.features

    @pytest.mark.parametrize("count", [1, 0, 2.5, 5000, True])
    async def test_the_count_must_be_a_sensible_whole_number(self, make_server, fake, count):
        result = await call(
            make_server(),
            "circular_pattern",
            {**BASE, "count": count, "axis": "J1", "feature_ids": ["F1"]},
            expect_error=True,
        )
        assert result.is_error and not fake.features

    async def test_linear_pattern_in_a_world_direction(self, make_server, fake):
        self.grow(fake)
        server = make_server()
        base = {**BASE, "count": 4, "distance": 20, "feature_ids": ["F1"]}
        out = (await call(server, "linear_pattern", {**base, "direction": "z"})).structured_content
        assert out["name"] == "Linear pattern x4 @ 20 mm"
        p = feature_params(fake.features[0])
        assert fake.features[0]["featureType"] == "linearPattern"
        assert ids_of(p["directionOne"]) == ["JDC"] and p["oppositeDirection"]["value"] is False
        assert p["distance"]["expression"] == "20 mm"
        assert (p["instanceCount"]["expression"], p["instanceCount"]["isInteger"]) == ("4", True)
        assert "hasSecondDir" not in p
        # +y is the Front plane's normal reversed; asking for "opposite" flips it back
        await call(server, "linear_pattern", {**base, "direction": "y"})
        p = feature_params(fake.features[1])
        assert ids_of(p["directionOne"]) == ["JCC"] and p["oppositeDirection"]["value"] is True
        await call(server, "linear_pattern", {**base, "direction": "y", "opposite_direction": True})
        assert feature_params(fake.features[2])["oppositeDirection"]["value"] is False

    async def test_linear_pattern_grid_and_edge_direction(self, make_server, fake):
        self.grow(fake)
        out = (
            await call(
                make_server(),
                "linear_pattern",
                {
                    **BASE,
                    "count": 4,
                    "distance": "#pitch",
                    "direction": "JEDGE",
                    "part_ids": ["JHD"],
                    "count_2": 3,
                    "distance_2": 15,
                    "direction_2": "x",
                },
            )
        ).structured_content
        p = feature_params(fake.features[0])
        assert ids_of(p["directionOne"]) == ["JEDGE"] and p["distance"]["expression"] == "#pitch"
        assert p["hasSecondDir"]["value"] is True and ids_of(p["directionTwo"]) == ["JEC"]
        assert p["distanceTwo"]["expression"] == "15 mm"
        assert (p["instanceCountTwo"]["expression"], p["instanceCountTwo"]["isInteger"]) == (
            "3",
            True,
        )
        assert out["name"] == "Linear pattern x4 @ (pitch), x3 @ 15 mm"

    async def test_a_second_direction_needs_all_three_values(self, make_server, fake):
        result = await call(
            make_server(),
            "linear_pattern",
            {
                **BASE,
                "count": 4,
                "distance": 20,
                "direction": "x",
                "feature_ids": ["F1"],
                "count_2": 3,
            },
            expect_error=True,
        )
        assert "second direction needs" in text_of(result) and not fake.features

    async def test_mirror_across_an_origin_plane_or_a_face(self, make_server, fake):
        self.grow(fake)
        server = make_server()
        out = (
            await call(server, "mirror", {**BASE, "plane": "Right", "part_ids": ["JHD"]})
        ).structured_content
        assert out["name"] == "Mirror (Right)"
        p = feature_params(fake.features[0])
        assert fake.features[0]["featureType"] == "mirror"
        assert (p["patternType"]["value"], p["patternType"]["enumName"]) == ("PART", "MirrorType")
        assert ids_of(p["mirrorPlane"]) == ["JEC"] and p["operationType"]["value"] == "NEW"
        await call(server, "mirror", {**BASE, "plane": "JHF", "feature_ids": ["F1"]})
        p = feature_params(fake.features[1])
        assert ids_of(p["mirrorPlane"]) == ["JHF"] and p["patternType"]["value"] == "FEATURE"

    async def test_verify_false_saves_the_measuring_requests(self, make_server, fake):
        out = (
            await call(
                make_server(),
                "mirror",
                {**BASE, "plane": "Top", "part_ids": ["JHD"], "verify": False},
            )
        ).structured_content
        assert out["status"] == "OK" and "effect" not in out
        assert not fake.calls("GET", "massproperties") and len(fake.features) == 1

    async def test_a_failed_measurement_does_not_lose_the_feature(self, make_server, fake):
        fake.fail_next = (500, {"message": "Internal error"})  # the first (measuring) request
        args = {**BASE, "workspace_id": WID, "plane": "JHF", "part_ids": ["JHD"]}
        out = (await call(make_server(), "mirror", args)).structured_content
        assert out["status"] == "OK" and "effect" not in out and len(fake.features) == 1

    async def test_new_parts_from_a_pattern_are_listed_with_a_naming_note(self, make_server, fake):
        def add_copy(feature):
            fake.parts.append({"partId": "JCOPY", "name": "Part 2", "bodyType": "solid"})
            fake.volumes["JCOPY"] = 8e-6

        fake.after_add = add_copy
        out = (
            await call(make_server(), "mirror", {**BASE, "plane": "Top", "part_ids": ["JHD"]})
        ).structured_content
        assert out["parts_created"] == [{"part_id": "JCOPY", "name": "Part 2"}]
        assert "placeholder name" in out["note"] and "warning" not in out
        assert out["effect"]["parts_before"] == 1 and out["effect"]["parts_after"] == 2


class TestShaping:
    async def test_revolve_a_profile_around_its_construction_line(self, make_server, fake):
        out = (
            await call(
                make_server(),
                "revolve",
                {**BASE, "sketch_feature_id": "F001", "part_name": "Pulley"},
            )
        ).structured_content
        assert out["status"] == "OK" and out["name"] == "Revolve 360 deg"
        feature = fake.features[0]
        p = feature_params(feature)
        assert feature["featureType"] == "revolve"
        assert p["entities"]["queries"][0]["featureId"] == "F001"
        assert p["operationType"]["value"] == "NEW" and ids_of(p["axis"]) == ["JGC"]
        assert p["fullRevolve"]["value"] is True and "angle" not in p
        assert out["parts_created"] == [{"part_id": "JP1", "name": "Pulley"}]
        assert fake.parts[-1]["name"] == "Pulley" and "warning" not in out

    async def test_a_partial_revolve_with_an_explicit_axis(self, make_server, fake):
        out = (
            await call(
                make_server(),
                "revolve",
                {
                    **BASE,
                    "sketch_feature_id": "F001",
                    "axis": "JEDGE",
                    "angle": 90,
                    "symmetric": True,
                    "operation": "remove",
                    "only_parts": ["JHD"],
                },
            )
        ).structured_content
        p = feature_params(fake.features[0])
        assert ids_of(p["axis"]) == ["JEDGE"]
        assert p["fullRevolve"]["value"] is False and p["angle"]["expression"] == "90 deg"
        assert p["symmetric"]["value"] is True
        assert p["defaultScope"]["value"] is False and ids_of(p["booleanScope"]) == ["JHD"]
        assert out["name"] == "Revolve 90 deg"
        assert not fake.calls("POST", "featurescript")  # an explicit ID needs no lookup

    async def test_an_unscoped_intersect_is_refused_when_there_are_several_parts(
        self, make_server, fake
    ):
        fake.parts.append({"partId": "JB", "name": "Part 2", "bodyType": "solid"})
        result = await call(
            make_server(),
            "revolve",
            {**BASE, "sketch_feature_id": "F001", "operation": "intersect"},
            expect_error=True,
        )
        assert "only_parts" in text_of(result) and not fake.features

    async def test_shell_opens_faces_or_hollows_parts(self, make_server, fake):
        fake.after_add = lambda feature: fake.volumes.update({"JHD": 3e-6})
        server = make_server()
        out = (
            await call(server, "shell", {**BASE, "thickness": 2, "remove_face_ids": ["JHF"]})
        ).structured_content
        assert out["name"] == "Shell 2 mm" and out["effect"]["volume_after"] == pytest.approx(3000)
        p = feature_params(fake.features[0])
        assert fake.features[0]["featureType"] == "shell"
        assert p["isHollow"]["value"] is False and ids_of(p["entities"]) == ["JHF"]
        assert p["thickness"]["expression"] == "2 mm" and "parts" not in p
        await call(
            server,
            "shell",
            {**BASE, "thickness": "#wall", "hollow_part_ids": ["JHD"], "opposite_direction": True},
        )
        p = feature_params(fake.features[1])
        assert p["isHollow"]["value"] is True and ids_of(p["parts"]) == ["JHD"]
        assert p["thickness"]["expression"] == "#wall" and p["oppositeDirection"]["value"] is True

    @pytest.mark.parametrize(
        "selection", [{}, {"remove_face_ids": ["F"], "hollow_part_ids": ["P"]}]
    )
    async def test_shell_needs_exactly_one_selection(self, make_server, fake, selection):
        result = await call(
            make_server(), "shell", {**BASE, "thickness": 2, **selection}, expect_error=True
        )
        assert "exactly one of remove_face_ids" in text_of(result) and not fake.features

    async def test_boolean_union_subtract_intersect(self, make_server, fake):
        fake.volumes["JB"] = 2e-6
        server = make_server()

        def merge(feature):
            fake.volumes.pop("JB", None)

        fake.after_add = merge
        out = (
            await call(server, "boolean", {**BASE, "operation": "union", "tools": ["JHD", "JB"]})
        ).structured_content
        assert out["name"] == "Union" and out["parts_removed"] == ["JB"]
        p = feature_params(fake.features[0])
        assert fake.features[0]["featureType"] == "booleanBodies"
        assert p["operationType"] == {
            "btType": "BTMParameterEnum-145",
            "enumName": "BooleanOperationType",
            "value": "UNION",
            "parameterId": "operationType",
        }
        assert ids_of(p["tools"]) == ["JHD", "JB"] and "targets" not in p

        fake.after_add = None
        out = (
            await call(
                server,
                "boolean",
                {
                    **BASE,
                    "operation": "subtract",
                    "targets": ["JHD"],
                    "tools": ["JB"],
                    "keep_tools": True,
                },
            )
        ).structured_content
        p = feature_params(fake.features[1])
        assert out["name"] == "Subtract" and p["operationType"]["value"] == "SUBTRACTION"
        assert ids_of(p["targets"]) == ["JHD"] and ids_of(p["tools"]) == ["JB"]
        assert p["keepTools"]["value"] is True
        await call(server, "boolean", {**BASE, "operation": "intersect", "tools": ["JHD", "JB"]})
        assert feature_params(fake.features[2])["operationType"]["value"] == "INTERSECTION"

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            ({"operation": "subtract", "tools": ["B"]}, "needs `targets`"),
            ({"operation": "union", "tools": ["A"]}, "at least two parts"),
            (
                {"operation": "union", "tools": ["A", "B"], "targets": ["C"]},
                "only used by 'subtract'",
            ),
        ],
    )
    async def test_boolean_arguments_are_checked(self, make_server, fake, args, message):
        result = await call(make_server(), "boolean", {**BASE, **args}, expect_error=True)
        assert message in text_of(result) and not fake.features


class TestUiMadeConfigurations:
    """Inputs made in Onshape's own UI have a generated ID that differs from the label shown there
    (and options likewise). This is the shape read from a real hand-made 3Spoke / 6Spoke input."""

    LIST_ID, LENGTH_ID = "List_gfWXlOaT2Zmfyo", "Length_abc123"
    LIST = {
        "btType": "BTMConfigurationParameterEnum-105",
        "parameterId": LIST_ID,
        "parameterName": "Configuration",
        "enumName": f"{LIST_ID}_conf",
        "namespace": "",
        "defaultValue": "Default",
        "options": [
            {"btType": "BTMEnumOption-592", "option": "Default", "optionName": "Default"},
            {"btType": "BTMEnumOption-592", "option": "_3Spoke", "optionName": "3Spoke"},
            {"btType": "BTMEnumOption-592", "option": "_6Spoke", "optionName": "6Spoke"},
        ],
    }
    LENGTH = {
        "btType": "BTMConfigurationParameterQuantity-1826",
        "parameterId": LENGTH_ID,
        "parameterName": "Length",
        "quantityType": "LENGTH",
        "rangeAndDefault": {"minValue": 0.01, "maxValue": 0.2, "defaultValue": 0.04},
    }

    def arrange(self, fake):
        fake.config_params = [self.LIST, self.LENGTH]

    async def sent_configuration(self, server, fake, configuration):
        await call(server, "get_part_properties", {**BASE, "configuration": configuration})
        return fake.calls("GET", "boundingboxes")[-1].url.params["configuration"]

    async def test_inputs_are_listed_by_label_with_their_ids(self, make_server, fake):
        self.arrange(fake)
        out = (await call(make_server(), "get_configuration", BASE)).structured_content
        listed, length = out["inputs"]
        assert listed == {
            "name": "Configuration",
            "id": self.LIST_ID,
            "type": "list",
            "options": ["Default", "3Spoke", "6Spoke"],
            "default": "Default",
            "option_ids": {"Default": "Default", "3Spoke": "_3Spoke", "6Spoke": "_6Spoke"},
        }
        assert (length["name"], length["id"], length["type"]) == (
            "Length",
            self.LENGTH_ID,
            "length",
        )
        assert length["default"] == pytest.approx(40)

    async def test_labels_and_ids_both_reach_onshape_as_ids(self, make_server, fake):
        self.arrange(fake)
        server = make_server()
        by_label = await self.sent_configuration(
            server, fake, {"Configuration": "3Spoke", "Length": 40}
        )
        assert by_label == f"{self.LIST_ID}=_3Spoke;{self.LENGTH_ID}=40 mm"
        by_id = await self.sent_configuration(server, fake, {self.LIST_ID: "_6Spoke"})
        assert by_id == f"{self.LIST_ID}=_6Spoke"
        assert await self.sent_configuration(server, fake, {"configuration": "6SPOKE"}) == by_id

    @pytest.mark.parametrize(
        ("configuration", "message"),
        [
            ({"Colour": "red"}, r"no configuration input 'Colour'.*'Configuration', 'Length'"),
            ({"Configuration": "9Spoke"}, r"no option '9Spoke'.*'Default', '3Spoke', '6Spoke'"),
        ],
    )
    async def test_unknown_names_are_explained_and_nothing_is_measured(
        self, make_server, fake, configuration, message
    ):
        self.arrange(fake)
        result = await call(
            make_server(),
            "get_part_properties",
            {**BASE, "configuration": configuration},
            expect_error=True,
        )
        assert re.search(message, text_of(result))
        assert not fake.calls("GET", "boundingboxes")

    async def test_the_definition_is_read_once_for_several_calls(self, make_server, fake):
        self.arrange(fake)
        server = make_server()
        for option in ("3Spoke", "6Spoke", "Default"):
            await self.sent_configuration(server, fake, {"Configuration": option})
        assert len(fake.calls("GET", "/configuration")) == 1

    async def test_screenshots_and_body_details_resolve_names_too(self, make_server, fake):
        self.arrange(fake)
        server = make_server()
        cfg = {"configuration": {"Configuration": "6Spoke"}}
        await call(server, "get_body_details", {**BASE, **cfg})
        await call(server, "capture_screenshot", {**BASE, **cfg})
        for pattern in ("bodydetails", "shadedviews"):
            sent = fake.calls("GET", pattern)[0].url.params["configuration"]
            assert sent == f"{self.LIST_ID}=_6Spoke", pattern

    async def test_an_instance_is_inserted_in_the_variant_named_by_label(self, make_server, fake):
        self.arrange(fake)
        await call(
            make_server(),
            "insert_instance",
            {
                "document_id": DID,
                "assembly_id": AID,
                "source_element_id": EID,
                "part_id": "JHD",
                "configuration": {"Configuration": "6Spoke"},
            },
        )
        assert fake.inserted[-1]["configuration"] == f"{self.LIST_ID}=_6Spoke"

    async def test_a_variable_can_depend_on_an_input_made_in_the_ui(self, make_server, fake):
        self.arrange(fake)
        server = make_server()
        out = (
            await call(
                server,
                "set_variable",
                {
                    **BASE,
                    "name": "spokes",
                    "kind": "number",
                    "by_option": {"Configuration": {"Default": 0, "3Spoke": 3, "_6Spoke": 6}},
                },
            )
        ).structured_content
        assert out["status"] == "OK"
        value = next(p for p in fake.features[0]["parameters"] if p["parameterId"] == "numberValue")
        assert value["configurationParameterId"] == self.LIST_ID  # the ID, not the label
        inner = {v["enumValue"]: v for v in value["values"]}
        assert set(inner) == {"Default", "_3Spoke", "_6Spoke"}
        assert inner["_6Spoke"]["value"]["expression"] == "6"
        assert inner["_3Spoke"]["enumName"] == f"{self.LIST_ID}_conf"
        listed = (await call(server, "list_variables", BASE)).structured_content["variables"]
        assert listed[0]["configured_by"] == "Configuration"
        assert listed[0]["by_option"] == {"Default": "0", "3Spoke": "3", "6Spoke": "6"}

    async def test_a_missing_option_value_names_the_labels(self, make_server, fake):
        self.arrange(fake)
        result = await call(
            make_server(),
            "set_variable",
            {**BASE, "name": "n", "kind": "number", "by_option": {"Configuration": {"3Spoke": 3}}},
            expect_error=True,
        )
        assert "missing: ['Default', '6Spoke']" in text_of(result) and not fake.features

    async def test_replacing_an_input_by_its_label(self, make_server, fake):
        self.arrange(fake)
        await call(
            make_server(),
            "set_configuration_input",
            {**BASE, "name": "Configuration", "kind": "list", "options": ["A", "B"]},
        )
        assert [p["parameterId"] for p in fake.config_params] == [self.LENGTH_ID, "Configuration"]


class TestVariables:
    @staticmethod
    def values(feature):
        return {p["parameterId"]: p for p in feature["parameters"]}

    async def test_set_variable_creates_then_updates_in_place(self, make_server, fake):
        server = make_server()
        created = (
            await call(server, "set_variable", {**BASE, "name": "shank_d", "value": 10})
        ).structured_content
        assert created["created"] is True and created["status"] == "OK"
        assert created["variable"] == "shank_d" and created["expression"] == "10 mm"
        feature = fake.features[0]
        # named after the variable, not "#shank_d": Onshape shows "?" for a name starting with "#"
        assert feature["featureType"] == "assignVariable" and feature["name"] == "shank_d"
        p = self.values(feature)
        assert p["name"]["value"] == "shank_d" and p["variableType"]["value"] == "LENGTH"
        assert p["lengthValue"]["expression"] == "10 mm" and p["mode"]["value"] == "ASSIGNED"

        updated = (
            await call(server, "set_variable", {**BASE, "name": "shank_d", "value": "18 mm"})
        ).structured_content
        assert updated["created"] is False and updated["feature_id"] == created["feature_id"]
        assert len(fake.features) == 1
        assert self.values(fake.features[0])["lengthValue"]["expression"] == "18 mm"

    async def test_variable_kinds_use_the_right_expression(self, make_server, fake):
        server = make_server(ONSHAPE_UNITS="imperial")
        await call(server, "set_variable", {**BASE, "name": "len", "value": 2})
        await call(server, "set_variable", {**BASE, "name": "ang", "value": 45, "kind": "angle"})
        await call(server, "set_variable", {**BASE, "name": "n", "value": 4, "kind": "number"})
        await call(server, "set_variable", {**BASE, "name": "half", "value": "#len / 2"})
        by_name = {f["parameters"][2]["value"]: self.values(f) for f in fake.features}
        assert by_name["len"]["lengthValue"]["expression"] == "2 in"
        assert by_name["ang"]["angleValue"]["expression"] == "45 deg"
        assert by_name["n"]["numberValue"]["expression"] == "4"
        assert by_name["half"]["lengthValue"]["expression"] == "#len / 2"

    async def test_bad_variable_names_are_rejected(self, make_server, fake):
        for bad in ("2fast", "has space", "x;y", ""):
            await call(
                make_server(), "set_variable", {**BASE, "name": bad, "value": 1}, expect_error=True
            )
        assert not fake.requests

    async def test_list_variables_shows_expression_and_value_in_the_configured_unit(
        self, make_server
    ):
        server = make_server()
        await call(server, "set_variable", {**BASE, "name": "shank_d", "value": 10})
        await call(server, "set_variable", {**BASE, "name": "tilt", "value": 30, "kind": "angle"})
        listed = (await call(server, "list_variables", BASE)).structured_content["variables"]
        by_name = {v["name"]: v for v in listed}
        assert by_name["shank_d"]["value"] == 10.0 and by_name["shank_d"]["unit"] == "mm"
        assert (
            by_name["shank_d"]["expression"] == "10 mm" and by_name["shank_d"]["type"] == "length"
        )
        assert by_name["tilt"]["value"] == 30.0 and by_name["tilt"]["unit"] == "deg"

    async def test_sketch_sizes_can_be_variables(self, make_server, fake):
        server = make_server()
        await call(server, "set_variable", {**BASE, "name": "shank_d", "value": 12})
        await call(server, "set_variable", {**BASE, "name": "af", "value": 16})
        shapes = [
            {"type": "circle", "center": [0, 0], "diameter": "#shank_d"},
            {"type": "hexagon", "center": [40, 0], "across_flats": "#af"},
        ]
        await call(server, "create_sketch", {**BASE, "plane": "Top", "shapes": shapes})
        sketch = fake.features[-1]
        dims = {
            c["constraintType"]: c
            for c in sketch["constraints"]
            if c["constraintType"] in ("DIAMETER",)
        }
        expressions = sorted(
            p["expression"]
            for c in sketch["constraints"]
            for p in c["parameters"]
            if "expression" in p
        )
        assert expressions == ["#af / cos(30 deg)", "#shank_d"]
        assert dims["DIAMETER"]["parameters"][1]["parameterId"] == "length"
        radii = [e["geometry"]["radius"] for e in sketch["entities"] if "radius" in e["geometry"]]
        assert radii[0] == pytest.approx(0.006)  # the circle starts at the variable's current value

    async def test_an_undefined_variable_is_refused_before_anything_is_sent(
        self, make_server, fake
    ):
        shapes = [{"type": "circle", "center": [0, 0], "diameter": "#nope"}]
        result = await call(
            make_server(),
            "create_sketch",
            {**BASE, "plane": "JDC", "shapes": shapes},
            expect_error=True,
        )
        assert "#nope" in text_of(result) and "set_variable" in text_of(result)
        assert not fake.calls("POST", "/features$")


class TestNaming:
    NAME_ID = "57f3fb8efa3416c06701d60d"

    async def test_default_names_are_flagged_until_renamed(self, make_server):
        server = make_server()
        doc = (await call(server, "get_document", {"document_id": DID})).structured_content
        assert all(e.get("has_default_name") for e in doc["elements"])  # Part Studio 1, Assembly 1
        parts = (await call(server, "list_parts", BASE)).structured_content["parts"]
        assert parts[0]["name"] == "Part 1" and parts[0]["has_default_name"] is True

        await call(server, "rename", {**BASE, "name": "M10 Fasteners"})
        await call(server, "rename", {**BASE, "part_id": "JHD", "name": "M10x40 Hex Bolt"})
        doc = (await call(server, "get_document", {"document_id": DID})).structured_content
        studio = next(e for e in doc["elements"] if e["element_id"] == EID)
        assert studio["name"] == "M10 Fasteners" and "has_default_name" not in studio
        parts = (await call(server, "list_parts", BASE)).structured_content["parts"]
        assert parts[0]["name"] == "M10x40 Hex Bolt" and "has_default_name" not in parts[0]

    async def test_rename_sends_the_name_property_to_the_right_resource(self, make_server, fake):
        server = make_server()
        await call(server, "rename", {**BASE, "name": "Bolt tab"})
        await call(server, "rename", {**BASE, "part_id": "JHD", "name": "Bolt"})
        tab, part = fake.calls("POST", "/metadata/")
        assert tab.url.path.endswith(f"/e/{EID}") and part.url.path.endswith(f"/e/{EID}/p/JHD")
        for request, expected in ((tab, "Bolt tab"), (part, "Bolt")):
            assert json.loads(request.content) == {
                "properties": [{"propertyId": self.NAME_ID, "value": expected}]
            }

    async def test_extrude_names_the_new_part_and_reports_it(self, make_server, fake):
        out = (
            await call(
                make_server(),
                "extrude",
                {**BASE, "sketch_feature_id": "S1", "depth": 5, "part_name": "Hex Nut"},
            )
        ).structured_content
        assert out["parts_created"] == [{"part_id": "JP1", "name": "Hex Nut"}]
        assert "note" not in out
        assert [p["name"] for p in fake.parts] == ["Part 1", "Hex Nut"]

    async def test_extrude_nudges_when_a_new_part_keeps_its_placeholder_name(self, make_server):
        out = (
            await call(make_server(), "extrude", {**BASE, "sketch_feature_id": "S1", "depth": 5})
        ).structured_content
        assert out["parts_created"] == [{"part_id": "JP1", "name": "Part 2"}]
        assert "rename" in out["note"]

    async def test_unscoped_intersect_is_refused_when_other_parts_would_be_deleted(
        self, make_server, fake
    ):
        fake.parts.append({"partId": "JP1", "name": "Hex Nut", "bodyType": "solid"})
        args = {**BASE, "sketch_feature_id": "S1", "depth": 5, "operation": "intersect"}
        refused = await call(make_server(), "extrude", args, expect_error=True)
        assert "only_parts" in text_of(refused) and "deletes" in text_of(refused)
        assert not fake.calls("POST", "/features$")
        await call(make_server(), "extrude", {**args, "only_parts": ["JP1"]})
        assert fake.calls("POST", "/features$")

    async def test_only_new_extrudes_touch_parts(self, make_server, fake):
        server = make_server()
        args = {**BASE, "sketch_feature_id": "S1", "depth": 5, "operation": "add"}
        out = (await call(server, "extrude", args)).structured_content
        assert "parts_created" not in out and not fake.calls("GET", r"/parts/")


class TestCustomFeatures:
    THREAD = {**BASE, "feature_type": "threadCreator"}

    @staticmethod
    def sent_feature(fake):
        return json.loads(fake.calls("POST", "/features$")[-1].content)["feature"]

    @staticmethod
    def by_id(feature):
        return {p["parameterId"]: p for p in feature["parameters"]}

    async def test_list_shows_only_custom_features_with_usable_parameters(self, make_server):
        data = (await call(make_server(), "list_custom_features", BASE)).structured_content
        assert data["source"] == "part_studio" and "hint" not in data
        assert [f["feature_type"] for f in data["custom_features"]] == ["threadCreator"]
        thread = data["custom_features"][0]
        assert thread["name"] == "Thread Creator"
        assert thread["namespace"] == THREAD_NAMESPACE
        params = {p["id"]: p for p in thread["parameters"]}
        assert params["pitch"] == {
            "id": "pitch",
            "name": "Pitch",
            "type": "quantity",
            "unit": "mm",
            "default": "1.5 mm",
        }
        assert params["kind"]["options"] == ["EXTERNAL", "INTERNAL"]
        assert params["kind"]["option_labels"] == {"EXTERNAL": "External", "INTERNAL": "Internal"}
        assert params["kind"]["default"] == "EXTERNAL"
        assert params["flank"]["unit"] == "deg"
        assert params["starts"]["type"] == "integer" and "unit" not in params["starts"]
        assert params["faces"]["type"] == "query" and "default" not in params["faces"]
        assert "tip" not in params  # a notice has no value to set
        assert thread["unsupported_parameters"] == [{"id": "profile", "type": "Array"}]

    async def test_list_reports_custom_features_already_used(self, make_server, fake):
        server = make_server()
        await call(server, "add_custom_feature", {**TestCustomFeatures.THREAD, "name": "M8"})
        await call(server, "extrude", {**BASE, "sketch_feature_id": "S1", "depth": 5})
        data = (await call(server, "list_custom_features", BASE)).structured_content
        assert data["used_in_this_part_studio"] == [
            {
                "feature_id": "F001",
                "name": "M8",
                "feature_type": "threadCreator",
                "namespace": THREAD_NAMESPACE,
                "status": "OK",
            }
        ]

    async def test_list_explains_what_to_do_when_nothing_is_available(self, make_server, fake):
        fake.part_studio_specs = [fake.part_studio_specs[0]]  # built-ins only
        data = (await call(make_server(), "list_custom_features", BASE)).structured_content
        assert data["custom_features"] == []
        assert "account setting" in data["hint"] and "feature_studio_url" in data["hint"]

    async def test_list_from_a_feature_studio_url(self, make_server, fake):
        fake.part_studio_specs = []  # the Part Studio knows nothing about the script
        args = {**BASE, "feature_studio_url": STUDIO_URL}
        data = (await call(make_server(), "list_custom_features", args)).structured_content
        assert data["source"] == "feature_studio"
        assert data["custom_features"][0]["feature_type"] == "threadCreator"
        path = fake.calls("GET", "featurespecs")[0].url.path
        assert path.endswith(f"/featurestudios/d/{DID}/v/{VID}/e/{FSID}/featurespecs")

    async def test_add_sends_namespace_and_spec_defaults_with_caller_values_on_top(
        self, make_server, fake
    ):
        out = (
            await call(
                make_server(),
                "add_custom_feature",
                {
                    **self.THREAD,
                    "parameters": {
                        "pitch": 2,
                        "flank": 45,
                        "kind": "INTERNAL",
                        "cosmetic": True,
                        "faces": ["JHF", "JHG"],
                    },
                },
            )
        ).structured_content
        assert out["status"] == "OK" and out["feature_id"] == "F001"
        assert out["namespace"] == THREAD_NAMESPACE
        feature = self.sent_feature(fake)
        assert feature["featureType"] == "threadCreator"
        assert feature["namespace"] == THREAD_NAMESPACE  # verbatim from Onshape's spec
        assert feature["btType"] == "BTMFeature-134"
        assert feature["name"] == "#label"  # the script's name template, which Onshape renders
        p = self.by_id(feature)
        assert p["pitch"]["expression"] == "2 mm" and "value" not in p["pitch"]
        assert p["flank"]["expression"] == "45 deg"
        assert p["kind"]["value"] == "INTERNAL"
        assert p["kind"]["enumName"] == "ThreadKind" and p["kind"]["namespace"] == THREAD_NAMESPACE
        assert p["cosmetic"]["value"] is True
        assert p["faces"]["queries"][0]["deterministicIds"] == ["JHF", "JHG"]
        # untouched parameters keep Onshape's own defaults, with parameter IDs filled in
        assert p["length"]["expression"] == "10 mm" and p["length"]["parameterId"] == "length"
        assert p["label"]["value"] == "M8"
        assert "nodeId" not in json.dumps(feature)
        assert "tip" not in p and "profile" not in p  # UI-only / no default: nothing to send

    async def test_add_converts_units_and_integers(self, make_server, fake):
        server = make_server(ONSHAPE_UNITS="imperial")
        params = {"pitch": 0.05, "starts": 3, "mass": "2 kg", "length": "12 mm", "label": "x"}
        await call(server, "add_custom_feature", {**self.THREAD, "parameters": params})
        p = self.by_id(self.sent_feature(fake))
        assert p["pitch"]["expression"] == "0.05 in"
        assert p["starts"]["expression"] == "3" and p["starts"]["isInteger"] is True
        assert p["mass"]["expression"] == "2 kg"
        assert p["length"]["expression"] == "12 mm"
        assert p["label"]["value"] == "x"

    async def test_add_from_a_feature_studio_url(self, make_server, fake):
        fake.part_studio_specs = []
        args = {**self.THREAD, "feature_studio_url": STUDIO_URL, "parameters": {"pitch": 1}}
        out = (await call(make_server(), "add_custom_feature", args)).structured_content
        assert out["status"] == "OK"
        assert self.sent_feature(fake)["namespace"] == THREAD_NAMESPACE

    async def test_list_reads_defaults_that_live_in_value_not_expression(self, make_server):
        data = (await call(make_server(), "list_custom_features", BASE)).structured_content
        params = {p["id"]: p for p in data["custom_features"][0]["parameters"]}
        assert params["diameter"]["default"] == "10 mm"  # 0.01 m
        assert params["tilt"]["default"] == "30 deg"  # pi/6 rad
        assert params["taper"]["default"] == "45 deg"  # value is already in degrees
        data = (
            await call(make_server(ONSHAPE_UNITS="imperial"), "list_custom_features", BASE)
        ).structured_content
        assert data["custom_features"][0]["parameters"][0]["default"] == "0.3937 in"

    async def test_add_leaves_the_specs_own_empty_expression_defaults_alone(
        self, make_server, fake
    ):
        await call(make_server(), "add_custom_feature", self.THREAD)
        p = self.by_id(self.sent_feature(fake))
        assert p["diameter"]["expression"] == "" and p["diameter"]["value"] == 0.01
        assert p["diameter"]["units"] == "meter" and p["diameter"]["parameterId"] == "diameter"

    async def test_status_is_read_back_because_the_post_reply_is_not_regenerated_yet(
        self, make_server, fake
    ):
        fake.late_states["F001"] = "ERROR"  # the POST reply still says OK
        out = (await call(make_server(), "add_custom_feature", self.THREAD)).structured_content
        assert out["status"] == "ERROR"
        assert "by hand" in out["hint"] and "add_feature" in out["hint"]

    async def test_feature_studio_in_another_document_gets_a_qualified_namespace(
        self, make_server, fake
    ):
        other = f"https://cad.onshape.com/documents/{OTHER_DID}/v/{VID}/e/{FSID}"
        qualified = f"d{OTHER_DID}::v{VID}::e{FSID}::m{'9' * 24}"
        listed = (
            await call(make_server(), "list_custom_features", {**BASE, "feature_studio_url": other})
        ).structured_content
        assert listed["custom_features"][0]["namespace"] == qualified
        args = {**self.THREAD, "feature_studio_url": other}
        out = (await call(make_server(), "add_custom_feature", args)).structured_content
        assert out["namespace"] == qualified
        sent = self.sent_feature(fake)
        assert sent["namespace"] == qualified
        # Enum parameters repeat the namespace. Live, leaving the local form in them (while the
        # feature itself was qualified) made the feature fail; they must match the feature's.
        assert self.by_id(sent)["kind"]["namespace"] == qualified
        # the namespace shown by the list can be passed back to choose between scripts
        await call(make_server(), "add_custom_feature", {**args, "namespace": qualified})

    async def test_feature_studio_in_another_document_needs_a_version_url(self, make_server, fake):
        workspace_url = f"https://cad.onshape.com/documents/{OTHER_DID}/w/{VID}/e/{FSID}"
        args = {**self.THREAD, "feature_studio_url": workspace_url}
        result = await call(make_server(), "add_custom_feature", args, expect_error=True)
        assert "version" in text_of(result)
        assert not fake.requests  # refused before any request
        listed = (
            await call(
                make_server(), "list_custom_features", {**BASE, "feature_studio_url": workspace_url}
            )
        ).structured_content
        assert "version" in listed["note"]
        assert listed["custom_features"][0]["namespace"] == THREAD_NAMESPACE  # not qualified

    async def test_add_reports_an_error_status_from_onshape(self, make_server, fake):
        fake.states["F001"] = "ERROR"
        out = (await call(make_server(), "add_custom_feature", self.THREAD)).structured_content
        assert out["status"] == "ERROR" and "delete_feature" in out["warning"]
        assert out["feature_id"] == "F001"  # still returned, so the model can fix or delete it

    @pytest.mark.parametrize(
        ("args", "message"),
        [
            ({"parameters": {"nope": 1}}, "Available"),
            ({"parameters": {"kind": "SIDEWAYS"}}, "Choose from"),
            ({"parameters": {"mass": 2}}, "Pass a string with a unit"),
            ({"parameters": {"cosmetic": "yes"}}, "true or false"),
            ({"parameters": {"profile": ["a"]}}, "cannot be set"),
            ({"parameters": {"faces": []}}, "list of face"),
            ({"feature_type": "missing"}, "threadCreator"),
            ({"feature_type": "extrude"}, "No custom feature of type 'extrude'"),
            ({"namespace": "someone-else"}, "No custom feature"),
        ],
    )
    async def test_bad_input_gives_an_actionable_error(self, make_server, fake, args, message):
        result = await call(
            make_server(), "add_custom_feature", {**self.THREAD, **args}, expect_error=True
        )
        assert message in text_of(result)
        assert not fake.calls("POST", "/features$")  # nothing was sent to Onshape

    async def test_two_features_with_one_type_need_a_namespace(self, make_server, fake):
        other = {**THREAD_SPEC, "namespace": "e" + "7" * 24 + "::m" + "8" * 24}
        fake.part_studio_specs.append(other)
        server = make_server()
        result = await call(server, "add_custom_feature", self.THREAD, expect_error=True)
        assert "share the type" in text_of(result)
        await call(server, "add_custom_feature", {**self.THREAD, "namespace": other["namespace"]})
        assert self.sent_feature(fake)["namespace"] == other["namespace"]

    async def test_bad_feature_studio_url_is_rejected_without_a_request(self, make_server, fake):
        args = {**BASE, "feature_studio_url": "https://evil.example/not-onshape"}
        result = await call(make_server(), "list_custom_features", args, expect_error=True)
        assert "Feature Studio tab" in text_of(result)
        assert not fake.requests

    async def test_update_feature_treats_integer_parameters_as_counts(self, make_server, fake):
        server = make_server()
        await call(server, "add_custom_feature", self.THREAD)
        await call(
            server,
            "update_feature",
            {**BASE, "feature_id": "F001", "parameters": {"starts": 2, "pitch": 3}},
        )
        p = self.by_id(fake.features[0])
        assert p["starts"]["expression"] == "2"
        assert p["pitch"]["expression"] == "3 mm"


class TestInspection:
    async def test_part_properties_are_converted_to_millimeters(self, make_server):
        data = (
            await call(make_server(), "get_part_properties", {**BASE, "tight": False})
        ).structured_content
        assert data["bounding_box"]["size"] == [40.0, 20.0, 10.0]
        part = data["parts"][0]
        assert part["volume"] == pytest.approx(8000.0)  # 8e-6 m^3 -> mm^3
        assert part["centroid"] == [20.0, 10.0, 5.0]
        assert part["mass_kg"] == 0.5

    async def test_each_part_is_measured_separately_with_a_total(self, make_server, fake):
        """Onshape adds all parts up as ONE body unless massAsGroup=false (seen live: two cubes
        reported as a single 16000 mm^3 'part')."""
        fake.volumes = {"JHD": 8e-6, "JB": 2e-6}
        data = (
            await call(make_server(), "get_part_properties", {**BASE, "tight": False})
        ).structured_content
        assert [p["part_id"] for p in data["parts"]] == ["JHD", "JB"]
        assert [round(p["volume"]) for p in data["parts"]] == [8000, 2000]
        assert data["total_volume"] == pytest.approx(10000)
        assert fake.calls("GET", "massproperties")[-1].url.params["massAsGroup"] == "false"
        # one part: nothing to add up, and the per-part endpoint has no such option
        fake.volumes = {"JHD": 8e-6}
        single = (
            await call(make_server(), "get_part_properties", {**BASE, "tight": False})
        ).structured_content
        assert "total_volume" not in single
        await call(make_server(), "get_part_properties", {**BASE, "part_id": "JHD", "tight": False})
        assert "massAsGroup" not in fake.calls("GET", "massproperties")[-1].url.params

    async def test_part_properties_use_the_tight_box_unless_asked_not_to(self, make_server, fake):
        server = make_server()
        tight = (await call(server, "get_part_properties", BASE)).structured_content
        assert tight["bounding_box"]["size"] == [40.0, 20.0, 9.5]  # tight z, not the REST 10
        loose_args = {**BASE, "tight": False}
        quick = (await call(server, "get_part_properties", loose_args)).structured_content
        assert quick["bounding_box"]["size"] == [40.0, 20.0, 10.0]
        part_args = {**BASE, "part_id": "JHD"}
        part = (await call(server, "get_part_properties", part_args)).structured_content
        assert part["bounding_box"]["size"] == [40.0, 20.0, 9.5]
        sent = json.loads(fake.calls("POST", "featurescript")[-1].content)
        assert sent["queries"] == {"part": ["JHD"]} and "queries.part" in sent["script"]

    async def test_part_properties_say_so_when_the_tight_box_is_unavailable(
        self, make_server, fake
    ):
        fake.tight_box_fails = True
        data = (await call(make_server(), "get_part_properties", BASE)).structured_content
        assert data["bounding_box"]["size"] == [40.0, 20.0, 10.0]  # the quick REST box
        assert "quick REST box" in data["bounding_box_note"]

    async def test_part_properties_for_a_single_part_uses_the_part_endpoints(
        self, make_server, fake
    ):
        data = (
            await call(
                make_server(), "get_part_properties", {**BASE, "part_id": "JHD", "tight": False}
            )
        ).structured_content
        assert data["bounding_box"]["size"] == [40.0, 20.0, 10.0]
        assert fake.calls("GET", r"/parts/.*/partid/JHD/boundingboxes$")
        assert fake.calls("GET", r"/parts/.*/partid/JHD/massproperties$")

    async def test_part_properties_in_inches(self, make_server):
        data = (
            await call(
                make_server(ONSHAPE_UNITS="imperial"),
                "get_part_properties",
                {**BASE, "tight": False},
            )
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

    async def test_get_assembly_omits_what_onshape_does_not_report(self, make_server, fake):
        # a whole Part Studio inserted as one instance came back live as just {id, name}
        fake.asm_instances = [{"id": "I9", "name": "Hub <1>"}]
        data = (
            await call(make_server(), "get_assembly", {"document_id": DID, "element_id": AID})
        ).structured_content
        assert data["instances"] == [{"instance_id": "I9", "name": "Hub <1>", "suppressed": False}]

    async def test_list_documents_page_size_is_capped_at_what_onshape_allows(self, make_server):
        result = await call(make_server(), "list_documents", {"limit": 50}, expect_error=True)
        assert result.is_error  # Onshape answers 400 above 20; the schema refuses it up front

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
