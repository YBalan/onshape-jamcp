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
        self.fail_next: tuple[int, dict[str, Any]] | None = None
        self.shaded_calls: list[dict[str, str]] = []
        self.translation_polls = 0
        self.anonymous_session = False
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
                    {"id": EID, "name": "Part Studio 1", "elementType": "PARTSTUDIO"},
                    {"id": AID, "name": "Assembly 1", "elementType": "ASSEMBLY"},
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
                        f["featureId"]: {"featureStatus": self.states.get(f["featureId"], "OK")}
                        for f in self.features
                    },
                    "rollbackIndex": -1,
                }
            )
        if m := re.fullmatch(rf"{base}/features/featureid/(.+)", path):
            fid = m.group(1)
            if method == "DELETE":
                self.features = [f for f in self.features if f["featureId"] != fid]
                return self._json({})
            body = json.loads(request.content)
            self.features = [body["feature"] if f["featureId"] == fid else f for f in self.features]
            return self._json({"feature": body["feature"], "featureState": {"featureStatus": "OK"}})
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
        if path == f"/assemblies/d/{DID}/w/{WID}/e/{AID}" and method == "GET":
            return self._json(
                {
                    "rootAssembly": {
                        "instances": [
                            {
                                "id": "I1",
                                "name": "Bracket <1>",
                                "type": "Part",
                                "partId": "JHD",
                                "elementId": EID,
                                "suppressed": False,
                            },
                        ],
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
            return self._json([{"partId": "JHD", "name": "Part 1", "bodyType": "solid"}])
        if path == f"{base}/bodydetails":
            return self._json(_BODY_DETAILS)
        return self._json({"message": f"fake: no route for {method} {path}"}, 404)

    def _add_feature(self, feature: dict[str, Any]) -> httpx.Response:
        self._counter += 1
        stored = {**feature, "featureId": f"F{self._counter:03d}"}
        self.features.append(stored)
        return self._json({"feature": stored, "featureState": {"featureStatus": "OK"}})


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
