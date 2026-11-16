import math

import pytest
from pydantic import TypeAdapter

from onshape_jamcp import custom_features, features, fs, sketch
from onshape_jamcp.errors import InputError
from onshape_jamcp.units import IMPERIAL, METRIC

ShapeList = TypeAdapter(list[sketch.Shape])


def build(shapes, units=METRIC, plane="JCC"):
    parsed = ShapeList.validate_python(shapes)
    return sketch.build_sketch_feature("S", plane, parsed, units)["feature"]


def lines(feature):
    return [e for e in feature["entities"] if e["geometry"]["btType"] == "BTCurveGeometryLine-117"]


class TestSketch:
    def test_envelope_and_plane(self):
        feature = build([{"type": "line", "start": [0, 0], "end": [10, 0]}], plane="JDC")
        assert feature["btType"] == "BTMSketch-151"
        assert feature["featureType"] == "newSketch"
        plane = feature["parameters"][0]
        assert plane["parameterId"] == "sketchPlane"
        assert plane["queries"][0]["deterministicIds"] == ["JDC"]

    def test_rectangle_is_metric_and_closed(self):
        feature = build([{"type": "rectangle", "corner1": [0, 0], "corner2": [40, 20]}])
        segments = lines(feature)
        assert len(segments) == 4
        # 40 mm x 20 mm -> metres
        lengths = sorted(round(s["endParam"], 6) for s in segments)
        assert lengths == [0.02, 0.02, 0.04, 0.04]
        assert segments[0]["geometry"]["pntX"] == 0.0
        # 4 coincident constraints close the loop
        assert len(feature["constraints"]) == 4
        assert {c["constraintType"] for c in feature["constraints"]} == {"COINCIDENT"}

    def test_rectangle_in_inches(self):
        feature = build([{"type": "rectangle", "corner1": [0, 0], "corner2": [2, 1]}], IMPERIAL)
        lengths = sorted(round(s["endParam"], 6) for s in lines(feature))
        assert lengths == [0.0254, 0.0254, 0.0508, 0.0508]

    def test_explicit_units_mix(self):
        feature = build([{"type": "line", "start": [0, 0], "end": ["1 in", "0 mm"]}])
        assert lines(feature)[0]["endParam"] == pytest.approx(0.0254)

    def test_circle_from_radius_and_diameter_agree(self):
        a = build([{"type": "circle", "center": [5, 5], "radius": 3}])
        b = build([{"type": "circle", "center": [5, 5], "diameter": 6}])
        for feature in (a, b):
            arcs = feature["entities"]
            assert len(arcs) == 2
            assert arcs[0]["geometry"]["radius"] == pytest.approx(0.003)
            assert arcs[0]["geometry"]["xCenter"] == pytest.approx(0.005)
            assert arcs[0]["endParam"] == pytest.approx(math.pi)
            assert arcs[1]["endParam"] == pytest.approx(2 * math.pi)
            assert len(feature["constraints"]) == 3

    def test_circle_needs_exactly_one_size(self):
        with pytest.raises(InputError, match="exactly one"):
            build([{"type": "circle", "center": [0, 0]}])
        with pytest.raises(InputError, match="exactly one"):
            build([{"type": "circle", "center": [0, 0], "radius": 1, "diameter": 2}])

    def test_arc_angles(self):
        feature = build(
            [{"type": "arc", "center": [0, 0], "radius": 10, "start_angle": 270, "end_angle": 90}]
        )
        arc = feature["entities"][0]
        assert arc["startParam"] == pytest.approx(math.radians(270))
        assert arc["endParam"] == pytest.approx(math.radians(90) + 2 * math.pi)  # wraps CCW
        assert arc["centerId"].endswith(".center")

    def test_full_sweep_arc_rejected(self):
        with pytest.raises(InputError, match="full circle"):
            build(
                [{"type": "arc", "center": [0, 0], "radius": 1, "start_angle": 0, "end_angle": 360}]
            )

    def test_polygon_closed_and_open(self):
        pts = [[0, 0], [10, 0], [10, 10]]
        closed = build([{"type": "polygon", "points": pts}])
        assert len(lines(closed)) == 3 and len(closed["constraints"]) == 3
        opened = build([{"type": "polygon", "points": pts, "closed": False}])
        assert len(lines(opened)) == 2 and len(opened["constraints"]) == 1

    def test_construction_flag(self):
        feature = build([{"type": "line", "start": [0, 0], "end": [1, 0], "construction": True}])
        assert feature["entities"][0]["isConstruction"] is True

    def test_entity_ids_are_unique_across_shapes(self):
        feature = build(
            [
                {"type": "rectangle", "corner1": [0, 0], "corner2": [5, 5]},
                {"type": "rectangle", "corner1": [10, 0], "corner2": [15, 5]},
                {"type": "circle", "center": [2, 2], "radius": 1},
                {"type": "circle", "center": [12, 2], "radius": 1},
            ]
        )
        ids = [e["entityId"] for e in feature["entities"]]
        assert len(ids) == len(set(ids)) == 12
        constraint_ids = [c["entityId"] for c in feature["constraints"]]
        assert len(constraint_ids) == len(set(constraint_ids))

    @pytest.mark.parametrize(
        "shape",
        [
            {"type": "rectangle", "corner1": [0, 0], "corner2": [0, 5]},
            {"type": "line", "start": [1, 1], "end": [1, 1]},
            {"type": "circle", "center": [0, 0], "radius": 0},
            {"type": "polygon", "points": [[0, 0], [1, 1]]},
            {"type": "circle", "center": ["x", 0], "radius": 1},
        ],
    )
    def test_degenerate_shapes_are_rejected(self, shape):
        with pytest.raises(InputError):
            build([shape])

    def test_unknown_shape_type_is_a_validation_error(self):
        with pytest.raises(ValueError):
            ShapeList.validate_python([{"type": "spline"}])

    def test_empty_and_oversized(self):
        with pytest.raises(InputError):
            sketch.build_sketch_feature("S", "JCC", [], METRIC)
        many = ShapeList.validate_python(
            [{"type": "line", "start": [0, 0], "end": [1, 0]}] * (sketch.MAX_SHAPES + 1)
        )
        with pytest.raises(InputError, match="At most"):
            sketch.build_sketch_feature("S", "JCC", many, METRIC)


class TestVariableDrivenShapes:
    def feature(self, shapes, variables=None):
        parsed = ShapeList.validate_python(shapes)
        return sketch.build_sketch_feature("S", "JDC", parsed, METRIC, variables)["feature"]

    @staticmethod
    def kinds(feature):
        return [c["constraintType"] for c in feature["constraints"]]

    def test_numbers_add_no_dimension_constraints(self):
        feature = self.feature([{"type": "circle", "center": [0, 0], "diameter": 10}])
        assert "DIAMETER" not in self.kinds(feature)

    def test_circle_diameter_and_radius_variables(self):
        diameter = self.feature(
            [{"type": "circle", "center": [0, 0], "diameter": "#d"}], {"d": 0.012}
        )
        dim = next(c for c in diameter["constraints"] if c["constraintType"] == "DIAMETER")
        assert dim["parameters"][0] == {
            "btType": "BTMParameterString-149",
            "value": "circle1.a",
            "parameterId": "localFirst",
        }
        assert dim["parameters"][1]["expression"] == "#d"
        assert diameter["entities"][0]["geometry"]["radius"] == pytest.approx(0.006)
        radius = self.feature([{"type": "circle", "center": [0, 0], "radius": "#r"}], {"r": 0.003})
        assert "RADIUS" in self.kinds(radius)
        assert radius["entities"][0]["geometry"]["radius"] == pytest.approx(0.003)

    def test_hexagon_is_a_regular_constrained_hexagon(self):
        feature = self.feature(
            [{"type": "hexagon", "center": [40, 0], "across_flats": "#af"}], {"af": 0.016}
        )
        kinds = self.kinds(feature)
        assert kinds.count("EQUAL") == 5 and kinds.count("HORIZONTAL") == 1
        assert kinds.count("FIX") == 1 and kinds.count("DIAMETER") == 1
        sides = [e for e in feature["entities"] if e["entityId"].startswith("poly")]
        assert len(sides) == 6
        xs = [e["geometry"]["pntX"] for e in sides]
        ys = [e["geometry"]["pntY"] for e in sides]
        assert max(xs) - min(xs) == pytest.approx(2 * 0.016 / math.sqrt(3))  # corners on the X axis
        assert max(ys) - min(ys) == pytest.approx(0.016)  # across flats, flats top and bottom
        dim = next(c for c in feature["constraints"] if c["constraintType"] == "DIAMETER")
        assert dim["parameters"][1]["expression"] == "#af / cos(30 deg)"
        # the corners sit on the construction circle
        on_circle = [
            c
            for c in feature["constraints"]
            if c["entityId"].endswith(tuple(f"oncircle{i}" for i in range(6)))
        ]
        assert len(on_circle) == 6

    def test_fixed_hexagon_has_no_dimension_constraint(self):
        feature = self.feature([{"type": "hexagon", "center": [0, 0], "across_flats": 16}])
        assert "DIAMETER" not in self.kinds(feature)

    def test_rectangle_width_and_height(self):
        feature = self.feature(
            [{"type": "rectangle", "corner1": [100, 0], "width": "#w", "height": 20}], {"w": 0.03}
        )
        kinds = self.kinds(feature)
        assert kinds.count("LENGTH") == 1 and kinds.count("HORIZONTAL") == 2
        assert kinds.count("VERTICAL") == 2 and kinds.count("FIX") == 1
        lines = [e for e in feature["entities"] if e["geometry"]["btType"].endswith("Line-117")]
        assert max(e["endParam"] for e in lines) == pytest.approx(0.03)

    def test_rectangle_needs_corner_or_size(self):
        with pytest.raises(InputError, match="corner2"):
            self.feature([{"type": "rectangle", "corner1": [0, 0], "width": 5}])
        with pytest.raises(InputError, match="not both"):
            self.feature(
                [
                    {
                        "type": "rectangle",
                        "corner1": [0, 0],
                        "corner2": [1, 1],
                        "width": 5,
                        "height": 5,
                    }
                ]
            )

    def test_an_unresolved_variable_is_an_input_error(self):
        with pytest.raises(InputError, match="#d is not defined"):
            self.feature([{"type": "circle", "center": [0, 0], "diameter": "#d"}])

    def test_referenced_variables(self):
        shapes = ShapeList.validate_python(
            [
                {"type": "circle", "center": [0, 0], "diameter": "#d"},
                {"type": "hexagon", "center": [0, 0], "across_flats": "#af"},
                {"type": "rectangle", "corner1": [0, 0], "width": "#w", "height": 5},
            ]
        )
        assert sketch.referenced_variables(shapes) == {"d", "af", "w"}
        assert sketch.variable_name("#a b") is None and sketch.variable_name("12 mm") is None


class TestFeatures:
    def params(self, call):
        return {p["parameterId"]: p for p in call["feature"]["parameters"]}

    def test_extrude_metric(self):
        call = features.extrude_feature(
            name="E", sketch_feature_id="SK1", depth=12.5, operation="remove", units=METRIC
        )
        assert call["btType"] == "BTFeatureDefinitionCall-1406"
        assert call["feature"]["featureType"] == "extrude"
        p = self.params(call)
        assert p["depth"]["expression"] == "12.5 mm"
        assert p["operationType"]["value"] == "REMOVE"
        assert p["oppositeDirection"]["value"] is False
        region = p["entities"]["queries"][0]
        assert region["featureId"] == "SK1"
        assert 'qSketchRegion(id + "SK1"' in region["queryString"]
        assert "endBound" not in p

    def test_extrude_imperial_symmetric_and_expression(self):
        call = features.extrude_feature(
            name="E",
            sketch_feature_id="S",
            depth="#h",
            operation="new",
            units=IMPERIAL,
            symmetric=True,
        )
        p = self.params(call)
        assert p["depth"]["expression"] == "#h"
        assert p["endBound"]["value"] == "SYMMETRIC"
        imperial = features.extrude_feature(
            name="E", sketch_feature_id="S", depth=2, operation="add", units=IMPERIAL
        )
        assert self.params(imperial)["depth"]["expression"] == "2 in"

    def test_extrude_can_be_restricted_to_parts(self):
        call = features.extrude_feature(
            name="E",
            sketch_feature_id="S",
            depth=1,
            operation="intersect",
            units=METRIC,
            only_parts=["JeD"],
        )
        p = self.params(call)
        assert p["defaultScope"]["value"] is False
        assert p["booleanScope"]["queries"][0]["deterministicIds"] == ["JeD"]
        unrestricted = features.extrude_feature(
            name="E", sketch_feature_id="S", depth=1, operation="add", units=METRIC
        )
        assert "booleanScope" not in self.params(unrestricted)

    def test_extrude_bad_operation(self):
        with pytest.raises(InputError):
            features.extrude_feature(
                name="E", sketch_feature_id="S", depth=1, operation="melt", units=METRIC
            )

    def test_fillet_and_chamfer(self):
        fillet = features.fillet_feature(name="F", edge_ids=["E1", "E2"], radius=3, units=METRIC)
        p = self.params(fillet)
        assert p["radius"]["expression"] == "3 mm"
        assert p["entities"]["queries"][0]["deterministicIds"] == ["E1", "E2"]
        chamfer = features.chamfer_feature(
            name="C", edge_ids=["E1"], distance="0.1 in", units=METRIC
        )
        p = self.params(chamfer)
        assert p["width"]["expression"] == "0.1 in"
        assert p["chamferType"]["value"] == "EQUAL_OFFSETS"

    def test_edge_ids_required(self):
        with pytest.raises(InputError):
            features.fillet_feature(name="F", edge_ids=[], radius=1, units=METRIC)

    def test_summarize_flags_errors(self):
        ok = features.summarize_feature_response(
            {
                "feature": {"featureId": "F1", "name": "X", "featureType": "extrude"},
                "featureState": {"featureStatus": "OK"},
            }
        )
        assert ok["status"] == "OK" and "warning" not in ok
        bad = features.summarize_feature_response(
            {"feature": {"featureId": "F2"}, "featureState": {"featureStatus": "ERROR"}}
        )
        assert "ERROR" in bad["warning"] and "delete_feature" in bad["warning"]


class TestFeatureScriptDecoding:
    def test_unwrap_live_flat_format(self):
        """Verbatim response shape captured from a live Onshape account (Top plane lookup)."""
        node = {
            "btType": "com.belmonttech.serialize.fsvalue.BTFSValueArray",
            "typeTag": "",
            "value": [
                {
                    "btType": "com.belmonttech.serialize.fsvalue.BTFSValueString",
                    "typeTag": "",
                    "value": "JDC",
                }
            ],
        }
        assert fs.unwrap(node) == ["JDC"]

    def test_unwrap_live_flat_map(self):
        prefix = "com.belmonttech.serialize.fsvalue."
        node = {
            "btType": prefix + "BTFSValueMap",
            "typeTag": "",
            "value": [
                {
                    "btType": prefix + "BTFSValueMapEntry",
                    "key": {"btType": prefix + "BTFSValueString", "typeTag": "", "value": "n"},
                    "value": {"btType": prefix + "BTFSValueNumber", "typeTag": "", "value": 3},
                }
            ],
        }
        assert fs.unwrap(node) == {"n": 3}

    def test_unwrap_array_of_strings(self):
        node = {
            "btType": "BTFSValueArray-1537",
            "message": {"value": [{"btType": "BTFSValueString-1422", "message": {"value": "JCC"}}]},
        }
        assert fs.unwrap(node) == ["JCC"]

    def test_unwrap_map_and_numbers(self):
        node = {
            "btType": "BTFSValueMap-2",
            "message": {
                "value": [
                    {
                        "btType": "BTFSValueMapEntry-1",
                        "message": {
                            "key": {"btType": "BTFSValueString-1", "message": {"value": "n"}},
                            "value": {"btType": "BTFSValueNumber-1", "message": {"value": 3}},
                        },
                    }
                ]
            },
        }
        assert fs.unwrap(node) == {"n": 3}

    def test_unwrap_passes_plain_values_through(self):
        assert fs.unwrap(None) is None
        assert fs.unwrap({"a": 1}) == {"a": 1}
        assert fs.unwrap([1, "x"]) == [1, "x"]

    def test_plane_script_is_whitelisted(self):
        assert 'makeId("Front")' in fs.plane_id_script("Front")
        with pytest.raises(ValueError):
            fs.plane_id_script('Top"), evil(')


class TestCustomFeatureHelpers:
    DID, VID, EID = "a" * 24, "b" * 24, "c" * 24

    @pytest.mark.parametrize("kind", ["w", "v", "m"])
    def test_feature_studio_url_ids(self, kind):
        url = f"https://company.onshape.com/documents/{self.DID}/{kind}/{self.VID}/e/{self.EID}?x=1"
        assert custom_features.parse_feature_studio_url(url) == (
            self.DID,
            kind,
            self.VID,
            self.EID,
        )

    @pytest.mark.parametrize(
        "url", ["", "https://cad.onshape.com/documents/short/w/x/e/y", "not a url", "/documents/"]
    )
    def test_bad_feature_studio_url(self, url):
        with pytest.raises(InputError, match="Feature Studio tab"):
            custom_features.parse_feature_studio_url(url)

    def test_qualify_namespace_adds_document_and_version(self):
        spec = {"featureType": "x", "namespace": f"e{self.EID}::m{'1' * 24}"}
        studio = (self.DID, "v", self.VID, self.EID)
        qualified = custom_features.qualify_namespace(spec, studio)
        assert qualified["namespace"] == f"d{self.DID}::v{self.VID}::e{self.EID}::m{'1' * 24}"
        assert spec["namespace"].startswith("e")  # the input is not modified
        done = {"namespace": qualified["namespace"]}
        assert custom_features.qualify_namespace(done, studio) is done  # already qualified
        nested = {
            "namespace": f"e{self.EID}::m{'1' * 24}",
            "parameters": [{"namespace": f"e{self.EID}::m{'1' * 24}", "options": ["a"]}],
        }
        out = custom_features.qualify_namespace(nested, studio)
        assert out["parameters"][0]["namespace"] == out["namespace"]  # enum copies follow suit
        assert out["parameters"][0]["options"] == ["a"]
        odd = {"namespace": "something-else"}
        assert custom_features.qualify_namespace(odd, studio) is odd  # unknown form: untouched

    def test_is_custom_follows_the_namespace(self):
        assert custom_features.is_custom({"namespace": "e1::m2"})
        assert not custom_features.is_custom({"namespace": ""})
        assert not custom_features.is_custom({})

    def test_builtin_feature_is_refused(self):
        spec = {"featureType": "extrude", "namespace": "", "parameters": []}
        with pytest.raises(InputError, match="built-in"):
            custom_features.build_custom_feature(spec, name=None, values={}, units=METRIC)

    def test_enum_without_a_default_is_built_from_the_spec(self):
        spec = {
            "featureType": "x",
            "namespace": "ns",
            "parameters": [
                {
                    "btType": "BTParameterSpecEnum-171",
                    "parameterId": "mode",
                    "enumName": "Mode",
                    "namespace": "ns",
                    "options": ["A", "B"],
                }
            ],
        }
        body = custom_features.build_custom_feature(
            spec, name="N", values={"mode": "B"}, units=METRIC
        )
        assert body["feature"]["parameters"] == [
            {
                "btType": "BTMParameterEnum-145",
                "enumName": "Mode",
                "namespace": "ns",
                "value": "B",
                "parameterId": "mode",
            }
        ]
        assert body["feature"]["name"] == "N"

    def test_number_expression(self):
        assert METRIC.number_expression(6) == "6"
        assert METRIC.number_expression(1.5) == "1.5"
        assert METRIC.number_expression(" #count * 2 ") == "#count * 2"
        for bad in (True, "", "rm -rf /;", float("inf")):
            with pytest.raises(InputError):
                METRIC.number_expression(bad)
