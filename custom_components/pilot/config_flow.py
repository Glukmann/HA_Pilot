"""Config flow for the Pilot integration."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import voluptuous as vol

from homeassistant import config_entries
from homeassistant.helpers.service_info.hassio import HassioServiceInfo
from homeassistant.config_entries import ConfigFlow, ConfigFlowResult
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import PilotApiClient
from .const import (
    CONF_RUNTIME_HOST,
    CONF_RUNTIME_PORT,
    CONF_RUNTIME_TOKEN,
    DEFAULT_RUNTIME_PORT,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    CannotConnect,
)
from .coordinator import PilotDataUpdateCoordinator


class PilotConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Pilot."""

    VERSION = 1

    def __init__(self) -> None:
        """Initialize flow state."""
        self._hassio_data: dict[str, Any] | None = None

    async def _async_validate_or_error(
        self, user_input: dict[str, Any]
    ) -> dict[str, str]:
        """Validate runtime connectivity; return error map (empty on success)."""
        client = PilotApiClient(
            user_input[CONF_RUNTIME_HOST],
            user_input[CONF_RUNTIME_PORT],
            user_input.get(CONF_RUNTIME_TOKEN, ""),
            async_get_clientsession(self.hass),
        )
        try:
            await client.async_validate()
        except CannotConnect:
            return {"base": "cannot_connect"}
        return {}

    def _async_set_unique_id_and_abort_if_configured(self) -> None:
        """Single-instance integration: one Pilot per Home Assistant."""
        self.context["unique_id"] = DOMAIN
        self._abort_if_unique_id_configured()

    async def async_step_user(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manual setup (local add-on address or a remote instance)."""
        errors: dict[str, str] = {}
        if user_input is not None:
            errors = await self._async_validate_or_error(user_input)
            if not errors:
                self._async_set_unique_id_and_abort_if_configured()
                return self.async_create_entry(title="Pilot", data=user_input)

        schema = vol.Schema(
            {
                vol.Required(CONF_RUNTIME_HOST): str,
                vol.Required(
                    CONF_RUNTIME_PORT, default=DEFAULT_RUNTIME_PORT
                ): vol.Coerce(int),
                vol.Optional(CONF_RUNTIME_TOKEN, default=""): str,
            }
        )
        return self.async_show_form(
            step_id="user", data_schema=schema, errors=errors
        )

    async def async_step_hassio(self, discovery_info: HassioServiceInfo) -> ConfigFlowResult:
        """Handle Supervisor discovery of the Pilot add-on."""
        self._async_set_unique_id_and_abort_if_configured()
        self._hassio_data = {
            CONF_RUNTIME_HOST: discovery_info.config["host"],
            CONF_RUNTIME_PORT: DEFAULT_RUNTIME_PORT,
            CONF_RUNTIME_TOKEN: "",
        }
        return await self.async_step_hassio_confirm()

    async def async_step_hassio_confirm(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Confirm Supervisor-discovered add-on."""
        assert self._hassio_data is not None
        if user_input is not None:
            return self.async_create_entry(title="Pilot", data=self._hassio_data)
        return self.async_show_form(step_id="hassio_confirm", data_schema=vol.Schema({}))

    @staticmethod
    def async_get_options_flow(
        config_entry: ConfigFlow,
    ) -> PilotOptionsFlowHandler:
        """Return the options flow handler."""
        return PilotOptionsFlowHandler()


class PilotOptionsFlowHandler(config_entries.OptionsFlow):
    """Handle Pilot options (e.g. polling interval)."""

    def __init__(self) -> None:
        """Initialize options flow state."""
        self._entry: config_entries.ConfigEntry | None = None

    async def async_step_init(
        self, user_input: dict[str, Any] | None = None
    ) -> ConfigFlowResult:
        """Manage the options."""
        if user_input is not None:
            coordinator: PilotDataUpdateCoordinator | None = (
                self.hass.config_entries.async_get_entry(
                    self.config_entry.entry_id
                ).runtime_data
                if self.config_entry.state == config_entries.ConfigEntryState.LOADED
                else None
            )
            if coordinator is not None:
                coordinator.update_interval = timedelta(
                    seconds=user_input["scan_interval"]
                )
            return self.async_create_entry(data=user_input)

        current = self.config_entry.options.get(
            "scan_interval", DEFAULT_SCAN_INTERVAL
        )
        schema = vol.Schema(
            {
                vol.Required(
                    "scan_interval", default=current
                ): vol.All(vol.Coerce(int), vol.Range(min=10, max=300)),
            }
        )
        return self.async_show_form(step_id="init", data_schema=schema)
