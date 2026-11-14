import math

import pytest
from pydantic import TypeAdapter

from onshape_jamcp import features, fs, sketch
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
