"""Shared application state: settings, the API client and small lookup caches."""

import httpx

from . import fs
from .client import OnshapeClient, q
from .config import Settings
from .errors import ConfigError, InputError, OnshapeError
from .units import UnitSystem


class App:
    """Owns the lazily created :class:`OnshapeClient` and caches stable lookups."""

    def __init__(
        self, settings: Settings, *, transport: httpx.AsyncBaseTransport | None = None
    ) -> None:
        self.settings = settings
        self._transport = transport
        self._client: OnshapeClient | None = None
        self._workspaces: dict[str, str] = {}
        self._planes: dict[tuple[str, str, str], str] = {}
        self._element_types: dict[tuple[str, str], str] = {}

    @property
    def units(self) -> UnitSystem:
        return self.settings.units

    @property
    def client(self) -> OnshapeClient:
        if self._client is None:
            self._client = OnshapeClient(self.settings, transport=self._transport)
        return self._client

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # --- lookups -------------------------------------------------------------------------------

    async def workspace(self, document_id: str, workspace_id: str | None = None) -> str:
        """Return ``workspace_id`` or the document's default workspace (cached)."""
        if workspace_id:
            return workspace_id
        if document_id not in self._workspaces:
            doc = await self.client.get(f"/documents/{q(document_id)}")
            default = (doc.get("defaultWorkspace") or {}).get("id")
            if not default:
                raise OnshapeError(
                    404, f"Document {document_id} has no default workspace; pass workspace_id."
                )
            self._workspaces[document_id] = default
        return self._workspaces[document_id]

    async def element_type(self, document_id: str, workspace_id: str, element_id: str) -> str:
        """Return the element's type (``PARTSTUDIO``, ``ASSEMBLY``, ``DRAWING``, ...)."""
        key = (document_id, element_id)
        if key not in self._element_types:
            elements = await self.client.get(
                f"/documents/d/{q(document_id)}/w/{q(workspace_id)}/elements",
                {"elementId": element_id},
            )
            for element in elements if isinstance(elements, list) else []:
                if element.get("id") == element_id:
                    self._element_types[key] = str(element.get("elementType") or "UNKNOWN")
                    break
            else:
                raise InputError(
                    f"Element {element_id} was not found in document {document_id}. "
                    f"Use get_document to list its elements."
                )
        return self._element_types[key]

    async def evaluate_featurescript(
        self, document_id: str, workspace_id: str, element_id: str, script: str
    ) -> dict:
        return await self.client.post(
            f"/partstudios/d/{q(document_id)}/w/{q(workspace_id)}/e/{q(element_id)}/featurescript",
            # `queries` is a map of name -> list of query strings (empty here), not an array.
            {"script": script, "queries": {}},
        )

    async def plane_id(
        self, document_id: str, workspace_id: str, element_id: str, plane: str
    ) -> str:
        """Resolve Top/Front/Right to an ID; anything else is taken as a face/plane ID."""
        canonical = {p.lower(): p for p in fs.STANDARD_PLANES}.get(plane.strip().lower())
        if canonical is None:
            if not plane.strip():
                raise InputError("plane must not be empty.")
            return plane.strip()
        key = (document_id, element_id, canonical)
        if key not in self._planes:
            response = await self.evaluate_featurescript(
                document_id, workspace_id, element_id, fs.plane_id_script(canonical)
            )
            ids = fs.unwrap(response.get("result"))
            if not (isinstance(ids, list) and len(ids) == 1 and isinstance(ids[0], str) and ids[0]):
                raise OnshapeError(
                    500,
                    f"Could not resolve the ID of the {canonical} plane (FeatureScript returned "
                    f"{ids!r}). Pass a plane/face ID from get_body_details instead.",
                )
            self._planes[key] = ids[0]
        return self._planes[key]

    # --- guards --------------------------------------------------------------------------------

    def require_writable(self, action: str) -> None:
        if self.settings.read_only:
            raise ConfigError(
                f"{action} is disabled: the server is running in read-only mode "
                f"(ONSHAPE_READ_ONLY=true)."
            )
