"""A tiny in-memory stand-in for the Onshape REST API, served through ``httpx.MockTransport``.

It implements just enough of the endpoints the tools use to run them end to end, and records every
request so tests can assert on what was actually sent.
"""

import base64
import json
import re
from typing import Any

import httpx

# 1x1 transparent PNG
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)
ACCESS_KEY = "test-access"
SECRET_KEY = "test-secret"
DID, WID, EID, AID = "d" * 24, "w" * 24, "e" * 24, "a" * 24
FSID, VID = "c" * 24, "b" * 24  # a Feature Studio element and a document version
OTHER_DID = "f" * 24  # another document, e.g. where a public Feature Script lives
THREAD_NAMESPACE = f"e{FSID}::m{'9' * 24}"  # opaque to the server: it must be passed through


def _fs_value_array(values: list[str]) -> dict[str, Any]:
    """FeatureScript result in the flat form the live API returns (captured from Onshape)."""
    prefix = "com.belmonttech.serialize.fsvalue."
    return {
        "btType": prefix + "BTFSValueArray",
        "typeTag": "",
        "value": [
            {"btType": prefix + "BTFSValueString", "typeTag": "", "value": v} for v in values
        ],
    }


class FakeOnshape:
    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.features: list[dict[str, Any]] = []
        self.states: dict[str, str] = {}
        # Status that only shows up when the feature list is read back (the POST reply says OK
        # because the feature has not been regenerated yet - seen live with a custom feature).
        self.late_states: dict[str, str] = {}
        self.config_params: list[dict[str, Any]] = []
        self.inserted: list[dict[str, Any]] = []
        self.asm_features: list[dict[str, Any]] = []
        self.mate_status = "OK"
        self.batch_updates: list[dict[str, Any]] = []
        self.asm_instances: list[dict[str, Any]] = [
            {
                "id": "I1",
                "name": "Bracket <1>",
                "type": "Part",
                "partId": "JHD",
                "elementId": EID,
                "suppressed": False,
            }
        ]
        self.tight_box_fails = False  # the FeatureScript answers with an error (no result)
        self.fail_next: tuple[int, dict[str, Any]] | None = None
        self.shaded_calls: list[dict[str, str]] = []
        self.translation_polls = 0
        self.anonymous_session = False
        self.part_studio_specs: list[dict[str, Any]] = [STANDARD_SPEC, THREAD_SPEC]
        self.studio_specs: list[dict[str, Any]] = [THREAD_SPEC]
        self.parts: list[dict[str, Any]] = [
            {"partId": "JHD", "name": "Part 1", "bodyType": "solid"}
        ]
        self.element_names: dict[str, str] = {EID: "Part Studio 1", AID: "Assembly 1"}
        self._counter = 0

    # --- helpers -------------------------------------------------------------------------------

    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self.handle)

    def calls(self, method: str, pattern: str) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == method and re.search(pattern, r.url.path)]

    @staticmethod
    def _json(data: Any, status: int = 200) -> httpx.Response:
        return httpx.Response(status, json=data)

    # --- routing -------------------------------------------------------------------------------

    def handle(self, request: httpx.Request) -> httpx.Response:  # noqa: C901 - flat router
        self.requests.append(request)
        path = request.url.path.removeprefix("/api/v17")
        method = request.method
        query = dict(request.url.params)

        if self.fail_next:
            status, body = self.fail_next
            self.fail_next = None
            return self._json(body, status)
        if not request.headers.get("authorization"):
            return self._json({"message": "Unauthorized"}, 401)

        if path == "/users/sessioninfo":
            if self.anonymous_session:  # what real Onshape does for unrecognised keys
                return self._json({})
            return self._json({"id": "user1", "name": "Test User"})
        if path == "/documents" and method == "GET":
            return self._json(
                {
                    "items": [
                        {
                            "id": DID,
                            "name": "Bracket",
                            "modifiedAt": "2026-01-01T00:00:00Z",
                            "owner": {"name": "Test User"},
                        }
                    ],
                    "next": None,
                }
            )
        if path == "/documents" and method == "POST":
            body = json.loads(request.content)
            return self._json({"id": DID, "name": body["name"], "defaultWorkspace": {"id": WID}})
        if path == f"/documents/{DID}":
            return self._json(
                {
                    "id": DID,
                    "name": "Bracket",
                    "defaultWorkspace": {"id": WID},
                    "modifiedAt": "2026-01-01T00:00:00Z",
                }
            )
        if path == f"/documents/d/{DID}/w/{WID}/elements":
            return self._json(
                [
                    {"id": EID, "name": self.element_names[EID], "elementType": "PARTSTUDIO"},
                    {"id": AID, "name": self.element_names[AID], "elementType": "ASSEMBLY"},
                ]
            )
        if path == f"/partstudios/d/{DID}/w/{WID}" and method == "POST":
            return self._json({"id": "f" * 24, "name": json.loads(request.content)["name"]})

        base = f"/partstudios/d/{DID}/w/{WID}/e/{EID}"
        # Per-part measurement endpoints answer exactly like the whole-studio ones here.
        path = re.sub(rf"^/parts/d/{DID}/w/{WID}/e/{EID}/partid/[^/]+/", f"{base}/", path)
        if path == f"{base}/featurescript":
            body = json.loads(request.content)
            if not isinstance(body.get("queries"), dict):  # real Onshape rejects an array (400)
                return self._json(
                    {"message": "Cannot deserialize `LinkedHashMap` from Array value"},
                    400,
                )
            script = body["script"]
            if "getVariable" in script:
                names = re.findall(r'getVariable\(context, "(\w+)"\)', script)
                defined = {
                    p["value"]
                    for f in self.features
                    if f.get("featureType") == "assignVariable"
                    for p in f["parameters"]
                    if p.get("parameterId") == "name"
                }
                if not set(names) <= defined:  # the script throws: no result, an ERROR notice
                    return self._json({"result": None, "notices": [{"level": "ERROR"}]})
                return self._json({"result": self._variable_map(script)})
            if "evBox3d" in script and self.tight_box_fails:
                return self._json({"result": None, "notices": [{"level": "ERROR"}]})
            if "evBox3d" in script:  # a tight box: z is 0.0095, tighter than the REST 0.01 below
                prefix = "com.belmonttech.serialize.fsvalue."
                numbers = [0.0, 0.0, 0.0, 0.04, 0.02, 0.0095]
                return self._json(
                    {
                        "result": {
                            "btType": prefix + "BTFSValueArray",
                            "typeTag": "",
                            "value": [
                                {"btType": prefix + "BTFSValueNumber", "typeTag": "", "value": n}
                                for n in numbers
                            ],
                        }
                    }
                )
            ids = {"Top": "JDC", "Front": "JCC", "Right": "JEC"}  # Top=JDC seen on live Onshape
            for name, plane_id in ids.items():
                if f'makeId("{name}")' in script:
                    return self._json({"result": _fs_value_array([plane_id])})
            return self._json(
                {
                    "result": {
                        "btType": "com.belmonttech.serialize.fsvalue.BTFSValueNumber",
                        "typeTag": "",
                        "value": 42,
                    }
                }
            )
        if path == f"{base}/features" and method == "POST":
            body = json.loads(request.content)
            return self._add_feature(body["feature"])
        if path == f"{base}/features" and method == "GET":
            return self._json(
                {
                    "features": self.features,
                    "featureStates": {
                        f["featureId"]: {
                            "featureStatus": self.late_states.get(
                                f["featureId"], self.states.get(f["featureId"], "OK")
                            )
                        }
                        for f in self.features
                    },
                    "rollbackIndex": -1,
                }
            )
        if path == f"{base}/features/updates" and method == "POST":
            # Like the live service: only the supplied parameters change; `name` is ignored.
            body = json.loads(request.content)
            self.batch_updates.append(body)
            changed = []
            for update in body["features"]:
                stored = next(f for f in self.features if f["featureId"] == update["featureId"])
                for new in update["parameters"]:
                    for i, old in enumerate(stored["parameters"]):
                        if old.get("parameterId") == new["parameterId"]:
                            stored["parameters"][i] = new
                changed.append(stored)
            states = {
                f["featureId"]: {"featureStatus": self.states.get(f["featureId"], "OK")}
                for f in changed
            }
            return self._json({"features": changed, "featureStates": states})
        if m := re.fullmatch(rf"{base}/features/featureid/(.+)", path):
            fid = m.group(1)
            if method == "DELETE":
                self.features = [f for f in self.features if f["featureId"] != fid]
                return self._json({})
            body = json.loads(request.content)
            self.features = [body["feature"] if f["featureId"] == fid else f for f in self.features]
            return self._json({"feature": body["feature"], "featureState": {"featureStatus": "OK"}})
        if m := re.fullmatch(rf"/metadata/d/{DID}/w/{WID}/e/(\w+)(?:/p/(\w+))?", path):
            if method == "POST":
                props = json.loads(request.content)["properties"]
                name = next(
                    p["value"] for p in props if p["propertyId"] == "57f3fb8efa3416c06701d60d"
                )
                if m.group(2):
                    for part in self.parts:
                        if part["partId"] == m.group(2):
                            part["name"] = name
                else:
                    self.element_names[m.group(1)] = name
            return self._json({"properties": []})
        if path == f"/elements/d/{DID}/w/{WID}/e/{EID}/configuration":
            if method == "POST":
                self.config_params = json.loads(request.content)["configurationParameters"]
            # the live service omits parameterType when it reads inputs back
            shown = [
                {k: v for k, v in x.items() if k != "parameterType"} for x in self.config_params
            ]
            return self._json({"configurationParameters": shown})
        if path == f"{base}/featurespecs":
            return self._json({"featureSpecs": self.part_studio_specs})
        studio_path = rf"/featurestudios/d/[0-9a-f]{{24}}/[wvm]/[^/]+/e/{FSID}/featurespecs"
        if re.fullmatch(studio_path, path):
            return self._json({"featureSpecs": self.studio_specs})
        if path == f"{base}/boundingboxes":
            return self._json(
                {"lowX": 0, "lowY": 0, "lowZ": 0, "highX": 0.04, "highY": 0.02, "highZ": 0.01}
            )
        if path == f"{base}/massproperties":
            return self._json(
                {
                    "bodies": {
                        "JHD": {
                            "mass": [0.5, 0.4, 0.6],
                            "volume": [8e-6, 7e-6, 9e-6],
                            "periphery": [0.0032, 0, 0],
                            "hasMass": True,
                            "centroid": [0.02, 0.01, 0.005],
                        }
                    }
                }
            )
        if path == f"{base}/shadedviews":
            self.shaded_calls.append(query)
            return self._json({"images": [base64.b64encode(PNG_1X1).decode()]})
        if path == f"/assemblies/d/{DID}/w/{WID}/e/{AID}/instances" and method == "POST":
            body = json.loads(request.content)
            self.inserted.append(body)
            n = len(self.asm_instances) + 1
            self.asm_instances.append(
                {
                    "id": f"I{n}",
                    "name": f"{body.get('partId') or 'Part Studio'} <{n}>",
                    "type": "Part" if body.get("partId") else "Part Studio",
                    "partId": body.get("partId"),
                    "elementId": body["elementId"],
                    "suppressed": False,
                }
            )
            return self._json({})  # Onshape answers with an empty body (seen live)
        if path == f"/assemblies/d/{DID}/w/{WID}/e/{AID}/features":
            if method == "POST":
                feature = json.loads(request.content)["feature"]
                stored = {**feature, "featureId": f"A{len(self.asm_features) + 1:03d}"}
                self.asm_features.append(stored)
                return self._json({"feature": stored, "featureState": {"featureStatus": "OK"}})
            return self._json(
                {
                    "features": self.asm_features,
                    "featureStates": {
                        f["featureId"]: {"featureStatus": self.mate_status}
                        for f in self.asm_features
                    },
                }
            )
        if path == f"/assemblies/d/{DID}/w/{WID}/e/{AID}" and method == "GET":
            return self._json(
                {
                    "rootAssembly": {
                        "instances": self.asm_instances,
                        "occurrences": [
                            {
                                "path": ["I1"],
                                "fixed": True,
                                "transform": [1, 0, 0, 0.1, 0, 1, 0, 0.2, 0, 0, 1, 0.3, 0, 0, 0, 1],
                            },
                        ],
                        "features": [
                            {
                                "id": "M1",
                                "featureType": "mate",
                                "suppressed": False,
                                "featureData": None,
                            },
                            {
                                "id": "M2",
                                "featureType": "mate",
                                "suppressed": True,
                                "featureData": {"name": "Fastened 1"},
                            },
                        ],
                    }
                }
            )
        if path == f"/assemblies/d/{DID}/w/{WID}/e/{AID}/shadedviews":
            self.shaded_calls.append(query)
            return self._json({"images": [[base64.b64encode(PNG_1X1).decode()]]})
        if path == f"{base}/stl":
            return httpx.Response(200, content=b"solid-binary-stl")
        if path == f"{base}/translations":
            return self._json({"id": "t1", "requestState": "ACTIVE"})
        if path == "/translations/t1":
            self.translation_polls += 1
            if self.translation_polls < 2:
                return self._json({"id": "t1", "requestState": "ACTIVE"})
            return self._json(
                {"id": "t1", "requestState": "DONE", "resultExternalDataIds": ["ext1"]}
            )
        if path == f"/documents/d/{DID}/externaldata/ext1":
            return httpx.Response(
                307, headers={"location": "https://cad.onshape.com/api/v17/blob/ext1"}
            )
        if path == "/blob/ext1":
            return httpx.Response(200, content=b"ISO-10303-21;STEP-FILE")
        if path == f"/parts/d/{DID}/w/{WID}/e/{EID}" and method == "GET":
            return self._json(self.parts)
        if path == f"{base}/bodydetails":
            return self._json(_BODY_DETAILS)
        return self._json({"message": f"fake: no route for {method} {path}"}, 404)

    def _variable_map(self, script: str) -> dict[str, Any]:
        """FeatureScript result for ``out["x"] = getVariable(context, "x") / meter;`` lines."""
        prefix = "com.belmonttech.serialize.fsvalue."
        entries = []
        for name, unit in re.findall(
            r'getVariable\(context, "(\w+)"\)( / meter| / degree)?;', script
        ):
            stored = next(
                f
                for f in self.features
                if f.get("featureType") == "assignVariable"
                and any(
                    p.get("parameterId") == "name" and p.get("value") == name
                    for p in f["parameters"]
                )
            )
            value_param = next(
                p for p in stored["parameters"] if p.get("parameterId", "").endswith("Value")
            )
            expression = (
                value_param.get("expression")
                or (
                    value_param["values"][0]["value"][
                        "expression"
                    ]  # configured: the default option
                )
            )
            number, _, symbol = expression.partition(" ")
            value = (
                float(number) * {"mm": 0.001, "in": 0.0254, "m": 1.0, "deg": 1.0, "": 1.0}[symbol]
            )
            if unit.strip() == "/ meter" and symbol == "":
                raise AssertionError(f"{name} is not a length")
            entries.append(
                {
                    "btType": prefix + "BTFSValueMapEntry",
                    "key": {"btType": prefix + "BTFSValueString", "typeTag": "", "value": name},
                    "value": {"btType": prefix + "BTFSValueNumber", "typeTag": "", "value": value},
                }
            )
        return {"btType": prefix + "BTFSValueMap", "typeTag": "", "value": entries}

    def _add_feature(self, feature: dict[str, Any]) -> httpx.Response:
        self._counter += 1
        stored = {**feature, "featureId": f"F{self._counter:03d}"}
        self.features.append(stored)
        is_new_extrude = feature.get("featureType") == "extrude" and any(
            p.get("parameterId") == "operationType" and p.get("value") == "NEW"
            for p in feature.get("parameters") or []
        )
        if is_new_extrude:  # a new body becomes a part with Onshape's placeholder name
            self.parts.append(
                {
                    "partId": f"JP{len(self.parts)}",
                    "name": f"Part {len(self.parts) + 1}",
                    "bodyType": "solid",
                }
            )
        status = self.states.get(stored["featureId"], "OK")
        return self._json({"feature": stored, "featureState": {"featureStatus": status}})


_BODY_DETAILS = {
    "bodies": [
        {
            "id": "JHD",
            "vertices": [
                {"id": "V1", "point": {"x": 0.0, "y": 0.0, "z": 0.0}},
                {"id": "V2", "point": {"x": 0.04, "y": 0.0, "z": 0.0}},
            ],
            "faces": [
                {
                    "id": "JHF",
                    "area": 0.0008,
                    "orientation": True,
                    "surface": {
                        "type": "plane",
                        "origin": {"x": 0, "y": 0, "z": 0.01},
                        "normal": {"x": 0, "y": 0, "z": 1},
                    },
                },
                {
                    "id": "JHG",
                    "area": 0.0008,
                    "orientation": False,
                    "surface": {
                        "type": "plane",
                        "origin": {"x": 0, "y": 0, "z": 0},
                        "normal": {"x": 0, "y": 0, "z": 1},
                    },
                },
            ],
            "edges": [{"id": "JHE", "curve": {"type": "line"}, "vertices": ["V1", "V2"]}],
        }
    ]
}


def _parameter_spec(kind: str, pid: str, name: str, default: dict | None = None, **extra) -> dict:
    spec = {"btType": f"BTParameterSpec{kind}", "parameterId": pid, "parameterName": name, **extra}
    if default is not None:
        spec["defaultValue"] = {**default, "parameterId": pid}
    return spec


# Feature specs as documented by the OpenAPI schema (BTFeatureSpec-129 / BTParameterSpec*). They
# have NOT been captured from a live account - see docs/development.md, "Verification status".
STANDARD_SPEC = {
    "btType": "BTFeatureSpec-129",
    "featureType": "extrude",
    "featureTypeName": "Extrude",
    "namespace": "",
    "parameters": [],
}
THREAD_SPEC = {
    "btType": "BTFeatureSpec-129",
    "featureType": "threadCreator",
    "featureTypeName": "Thread Creator",
    "featureTypeDescription": "Cuts or adds a helical thread on a cylindrical face.",
    "namespace": THREAD_NAMESPACE,
    # The UI stores this template as the feature name and renders it (live: "#screwfriendlyname").
    "featureNameTemplate": "#label",
    "parameters": [
        # Live specs often leave `expression` empty and put the number in `value` (SI units).
        _parameter_spec(
            "Quantity-173",
            "diameter",
            "Diameter",
            {
                "btType": "BTMParameterQuantity-147",
                "expression": "",
                "value": 0.01,
                "units": "meter",
            },
            quantityType="LENGTH",
        ),
        _parameter_spec(
            "Quantity-173",
            "tilt",
            "Tilt",
            {
                "btType": "BTMParameterQuantity-147",
                "expression": "",
                "value": 0.5235987755982988,
                "units": "radian",
            },
            quantityType="ANGLE",
        ),
        _parameter_spec(
            "Quantity-173",
            "taper",
            "End taper angle",
            {
                "btType": "BTMParameterQuantity-147",
                "expression": "",
                "value": 45.0,  # already degrees: the unit differs per parameter
                "units": "degree",
            },
            quantityType="ANGLE",
        ),
        _parameter_spec(
            "Query-174",
            "faces",
            "Cylindrical faces",
            {"btType": "BTMParameterQueryList-148", "queries": [], "nodeId": "spec-node"},
        ),
        _parameter_spec(
            "Enum-171",
            "kind",
            "Thread kind",
            {
                "btType": "BTMParameterEnum-145",
                "enumName": "ThreadKind",
                "namespace": THREAD_NAMESPACE,
                "value": "EXTERNAL",
            },
            enumName="ThreadKind",
            namespace=THREAD_NAMESPACE,
            options=["EXTERNAL", "INTERNAL"],
            optionNames=["External", "Internal"],
        ),
        _parameter_spec(
            "Quantity-173",
            "pitch",
            "Pitch",
            {"btType": "BTMParameterQuantity-147", "expression": "1.5 mm", "value": 0.0015},
            quantityType="LENGTH",
        ),
        _parameter_spec(
            "Quantity-173",
            "length",
            "Thread length",
            {"btType": "BTMParameterQuantity-147", "expression": "10 mm", "value": 0.01},
            quantityType="LENGTH",
        ),
        _parameter_spec(
            "Quantity-173",
            "flank",
            "Flank angle",
            {"btType": "BTMParameterQuantity-147", "expression": "30 deg"},
            quantityType="ANGLE",
        ),
        _parameter_spec(
            "Quantity-173",
            "starts",
            "Number of starts",
            {"btType": "BTMParameterQuantity-147", "expression": "1", "isInteger": True},
            quantityType="INTEGER",
        ),
        _parameter_spec(
            "Quantity-173",
            "mass",
            "Reference mass",
            {"btType": "BTMParameterQuantity-147", "expression": "1 kg"},
            quantityType="MASS",
        ),
        _parameter_spec(
            "Boolean-170",
            "cosmetic",
            "Cosmetic only",
            {"btType": "BTMParameterBoolean-144", "value": False},
        ),
        _parameter_spec(
            "String-175",
            "label",
            "Label",
            {"btType": "BTMParameterString-149", "value": "M8"},
        ),
        _parameter_spec(
            "Notice-3132",
            "tip",
            "Tip",
            {"btType": "BTMParameterNotice-4455", "value": "Pick a cylinder"},
        ),
        _parameter_spec("Array-2600", "profile", "Profile points"),
    ],
}
