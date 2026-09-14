"""Fixtures for Pilot tests."""

from homeassistant.setup import async_setup_component
import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in all tests."""
    return


@pytest.fixture(autouse=True)
async def setup_http(hass):
    """Load the http component (panel static paths registration needs it)."""
    assert await async_setup_component(hass, "http", {})
    yield
