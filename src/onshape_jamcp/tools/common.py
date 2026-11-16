"""Shared plumbing for tool modules: typed parameters and the tool registrar.

NOTE: tool modules deliberately do not use ``from __future__ import annotations`` - the MCP SDK
builds each tool's JSON schema from the *runtime* annotations.
"""

import functools
from collections.abc import Awaitable, Callable
from typing import Annotated, Any

from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError
from mcp_types import ToolAnnotations
from pydantic import Field

from ..app import App
from ..errors import JamcpError

DocumentId = Annotated[
    str,
    Field(
        min_length=1,
        description="Onshape document ID: the 24-character hex string after /documents/ in the "
        "document URL. Get it from list_documents.",
    ),
]
ElementId = Annotated[
    str,
    Field(
        min_length=1,
        description="Element (tab) ID: the string after /e/ in the Onshape URL. Get it from "
        "get_document.",
    ),
]
WorkspaceId = Annotated[
    str | None,
    Field(
        description="Workspace ID (the string after /w/ in the URL). Optional - defaults to the "
        "document's main workspace."
    ),
]
FeatureId = Annotated[
    str,
    Field(min_length=1, description="Feature ID as returned by list_features or a create tool."),
]

Configuration = Annotated[
    dict[str, str | float | int],
    Field(
        description="Configuration to evaluate, e.g. {'Size': 'M18'} (list inputs by option "
        "name, number inputs as a value). Omit for the default configuration."
    ),
]

Registrar = Callable[..., Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]]


def make_registrar(mcp: MCPServer, app: App) -> Registrar:
    """Return a ``@tool(...)`` decorator bound to ``mcp``.

    * domain errors (:class:`JamcpError`) become :class:`ToolError`, the only exception type whose
      message the MCP SDK shows to the model;
    * tools flagged ``writes=True`` are not registered at all in read-only mode.
    """

    def tool(
        *,
        title: str,
        read_only: bool = False,
        destructive: bool = False,
        idempotent: bool = False,
        writes: bool = False,
        structured: bool | None = None,
    ) -> Callable[[Callable[..., Awaitable[Any]]], Callable[..., Awaitable[Any]]]:
        annotations = ToolAnnotations(
            title=title,
            read_only_hint=read_only,
            destructive_hint=None if read_only else destructive,
            idempotent_hint=None if read_only else idempotent,
            open_world_hint=True,
        )

        def decorator(fn: Callable[..., Awaitable[Any]]) -> Callable[..., Awaitable[Any]]:
            @functools.wraps(fn)
            async def wrapper(*args: Any, **kwargs: Any) -> Any:
                try:
                    return await fn(*args, **kwargs)
                except JamcpError as exc:
                    raise ToolError(str(exc)) from None

            if writes and app.settings.read_only:
                return wrapper
            mcp.add_tool(
                wrapper,
                name=fn.__name__,
                title=title,
                annotations=annotations,
                structured_output=structured,
            )
            return wrapper

        return decorator

    return tool
