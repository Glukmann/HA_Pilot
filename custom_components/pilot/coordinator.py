"""DataUpdateCoordinator for the Pilot runtime."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import PilotApiClient
from .const import (
    CONF_RUNTIME_HOST,
    CONF_RUNTIME_PORT,
    CONF_RUNTIME_TOKEN,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    CannotConnect,
    PilotApiError,
)

_LOGGER = logging.getLogger(__name__)

type PilotConfigEntry = ConfigEntry[PilotDataUpdateCoordinator]


class PilotDataUpdateCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Poll the Pilot runtime status snapshot."""

    config_entry: PilotConfigEntry

    def __init__(self, hass: HomeAssistant, entry: PilotConfigEntry) -> None:
        """Initialize the coordinator."""
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=DOMAIN,
            update_interval=timedelta(
                seconds=entry.options.get("scan_interval", DEFAULT_SCAN_INTERVAL)
            ),
        )
        self.api = PilotApiClient(
            entry.data[CONF_RUNTIME_HOST],
            entry.data[CONF_RUNTIME_PORT],
            entry.data.get(CONF_RUNTIME_TOKEN, ""),
            async_get_clientsession(hass),
        )

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.api.async_get_status()
        except CannotConnect as err:
            raise UpdateFailed(f"Runtime unreachable: {err}") from err
        except PilotApiError as err:
            raise UpdateFailed(f"Runtime error: {err}") from err
