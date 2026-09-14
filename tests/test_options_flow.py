"""Test the Pilot options flow."""

from homeassistant.data_entry_flow import FlowResultType

from .test_integration import _mock_api, _setup_entry


async def test_options_flow_updates_scan_interval(hass, aioclient_mock):
    """Test changing scan_interval applies to the live coordinator."""
    _mock_api(aioclient_mock)
    entry = await _setup_entry(hass, aioclient_mock)
    assert entry.runtime_data.update_interval.total_seconds() == 30

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    assert result["step_id"] == "init"

    result = await hass.config_entries.options.async_configure(
        result["flow_id"], {"scan_interval": 60}
    )
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {"scan_interval": 60}
