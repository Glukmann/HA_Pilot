"""Constants for the Pilot integration."""

from __future__ import annotations

from typing import Final

DOMAIN: Final = "pilot"

CONF_RUNTIME_HOST: Final = "runtime_host"
CONF_RUNTIME_PORT: Final = "runtime_port"
CONF_RUNTIME_TOKEN: Final = "runtime_token"

DEFAULT_RUNTIME_PORT: Final = 8899
DEFAULT_SCAN_INTERVAL: Final = 30
REQUEST_TIMEOUT: Final = 10


class PilotApiError(Exception):
    """Base error raised by the Pilot runtime API client."""


class CannotConnect(PilotApiError):
    """Error raised when the Pilot runtime is unreachable or rejects auth."""
