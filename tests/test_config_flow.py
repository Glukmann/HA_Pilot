"""Test the Pilot config flow."""

from unittest.mock import AsyncMock, patch

from homeassistant.config_entries import SOURCE_HASSIO, SOURCE_USER
from homeassistant.data_entry_flow import FlowResultType
from homeassistant.helpers.service_info.hassio import HassioServiceInfo

from custom_components.pilot.const import DOMAIN

USER_INPUT = {
    "runtime_host": "pilot",
    "runtime_port": 8899,
    "runtime_token": "test-token",
}


async def test_user_flow_success(hass):
    """Test the manual user step creates an entry."""
    with (
        patch(
            "custom_components.pilot.config_flow.PilotApiClient.async_validate",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.pilot.async_setup_entry",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "user"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["title"] == "Pilot"
        assert result["data"] == USER_INPUT


async def test_user_flow_duplicate_abort(hass):
    """Test a duplicate entry aborts with single_instance_allowed."""
    with (
        patch(
            "custom_components.pilot.config_flow.PilotApiClient.async_validate",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.pilot.async_setup_entry",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
        assert result["type"] is FlowResultType.CREATE_ENTRY

        result2 = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result2 = await hass.config_entries.flow.async_configure(
            result2["flow_id"], USER_INPUT
        )
        assert result2["type"] is FlowResultType.ABORT
        assert result2["reason"] == "already_configured"


async def test_user_flow_cannot_connect_show_form(hass):
    """Test connection failure re-shows the form with an error."""
    from custom_components.pilot.const import CannotConnect

    with patch(
        "custom_components.pilot.config_flow.PilotApiClient.async_validate",
        new=AsyncMock(side_effect=CannotConnect("boom")),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], USER_INPUT
        )
        assert result["type"] is FlowResultType.FORM
        assert result["errors"] == {"base": "cannot_connect"}


async def test_hassio_discovery_creates_entry(hass):
    """Test Supervisor discovery flows through confirmation to an entry."""
    discovery = HassioServiceInfo(
        name="Pilot",
        slug="pilot",
        config={"host": "pilot", "port": 9999},
        uuid="test-uuid",
    )
    with patch(
        "custom_components.pilot.async_setup_entry", new=AsyncMock(return_value=True)
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_HASSIO}, data=discovery
        )
        assert result["type"] is FlowResultType.FORM
        assert result["step_id"] == "hassio_confirm"

        result = await hass.config_entries.flow.async_configure(result["flow_id"], {})
        assert result["type"] is FlowResultType.CREATE_ENTRY
        assert result["data"]["runtime_host"] == "pilot"
        assert result["data"]["runtime_port"] == 9999


async def test_hassio_discovery_abort_already_configured(hass):
    """Test discovery aborts when Pilot is already configured."""
    discovery = HassioServiceInfo(
        name="Pilot",
        slug="pilot",
        config={"host": "pilot", "port": 8899},
        uuid="test-uuid",
    )
    with (
        patch(
            "custom_components.pilot.config_flow.PilotApiClient.async_validate",
            new=AsyncMock(),
        ),
        patch(
            "custom_components.pilot.async_setup_entry",
            new=AsyncMock(return_value=True),
        ),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_USER}
        )
        await hass.config_entries.flow.async_configure(result["flow_id"], USER_INPUT)

        result2 = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": SOURCE_HASSIO}, data=discovery
        )
        assert result2["type"] is FlowResultType.ABORT
        assert result2["reason"] == "already_configured"
