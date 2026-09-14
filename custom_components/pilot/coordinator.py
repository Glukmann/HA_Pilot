"""DataUpdateCoordinator for the Pilot runtime."""

from __future__ import annotations

from datetime import timedelta
import logging
from typing import Any, cast

from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession
from homeassistant.helpers.storage import Store
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

STORAGE_VERSION = 1
STORAGE_KEY = f"{DOMAIN}.persona"

PERSONA_SLIDERS = (
    "butler_observer",
    "politeness",
    "verbosity",
    "conservative",
)

PERSONA_PRESETS = ("butler", "observer", "economy")
PILOT_MODES = ("normal", "vacation", "guests", "sick")


def _persona_defaults() -> dict[str, Any]:
    """Neutral persona defaults applied until the runtime reports real values."""
    return {
        "persona": {slider: 50 for slider in PERSONA_SLIDERS},
        "persona_preset": "butler",
        "mode": "normal",
        "current_focus": "",
    }


class PersonaStore(Store[dict[str, Any]]):
    """Persistent local cache of persona/policy state.

    The runtime is the source of truth; the Store mirrors the last known
    values so entities render sensible defaults before the first successful
    poll and survive runtime restarts.
    """


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
        self.store: PersonaStore = PersonaStore(hass, STORAGE_VERSION, STORAGE_KEY)
        self.persona_cache: dict[str, Any] = _persona_defaults()

    async def async_load_cache(self) -> None:
        """Load the persona cache from storage, falling back to defaults."""
        stored = await self.store.async_load()
        if stored:
            self.persona_cache = {**_persona_defaults(), **stored}

    async def async_save_cache(self) -> None:
        """Persist the persona cache."""
        await self.store.async_save(self.persona_cache)

    def persona_value(self, key: str) -> Any:
        """Return a cached persona/policy value (runtime data wins if present)."""
        data = self.data or {}
        if key == "persona":
            persona = data.get("persona")
            if isinstance(persona, dict):
                return {**self.persona_cache["persona"], **persona}
            return self.persona_cache["persona"]
        if key in data and data[key] is not None:
            return data[key]
        return self.persona_cache.get(key)

    async def async_set_persona(self, slider: str, value: int) -> None:
        """Write a persona slider through to the runtime and cache."""
        await self.api.async_set_persona(slider, value)
        cast(dict[str, Any], self.persona_cache["persona"])[slider] = value
        await self.async_save_cache()

    async def async_set_policy_value(self, key: str, value: Any) -> None:
        """Write a policy value (preset/mode/focus/budget) to the runtime and cache."""
        if key == "persona_preset":
            await self.api.async_set_persona_preset(str(value))
        elif key == "mode":
            await self.api.async_set_mode(str(value))
        elif key == "current_focus":
            await self.api.async_set_current_focus(str(value))
        elif key == "daily_budget":
            await self.api.async_set_daily_budget(float(value))
        else:  # pragma: no cover - guarded by entity platforms
            raise ValueError(f"Unknown policy key: {key}")
        self.persona_cache[key] = value
        await self.async_save_cache()

    async def _async_update_data(self) -> dict[str, Any]:
        try:
            return await self.api.async_get_status()
        except CannotConnect as err:
            raise UpdateFailed(f"Runtime unreachable: {err}") from err
        except PilotApiError as err:
            raise UpdateFailed(f"Runtime error: {err}") from err
