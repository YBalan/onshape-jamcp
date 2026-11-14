"""Exception types shared across the package."""

from typing import Any


class JamcpError(Exception):
    """Base class for errors whose message is safe and useful to show to the model."""


class ConfigError(JamcpError):
    """The server is misconfigured (missing credentials, invalid setting, ...)."""


class InputError(JamcpError):
    """A tool argument is invalid in a way the schema cannot express."""


class NetworkError(JamcpError):
    """The Onshape API could not be reached (DNS failure, timeout, TLS problem, ...)."""


class OnshapeError(JamcpError):
    """The Onshape REST API returned an error response."""

    def __init__(self, status: int, message: str, body: Any = None, *, hint: str | None = None):
        self.status = status
        self.message = message
        self.body = body
        self.hint = hint
        text = f"Onshape API error {status}: {message}"
        if hint:
            text += f" ({hint})"
        super().__init__(text)
