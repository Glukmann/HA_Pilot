"""Fixtures for Pilot tests."""

from pathlib import Path
import sys

from homeassistant.setup import async_setup_component
import pytest

ADDON_APP = str(Path(__file__).parent.parent / "pilot-addon" / "rootfs" / "app")
if ADDON_APP not in sys.path:
    sys.path.insert(0, ADDON_APP)


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(enable_custom_integrations):
    """Enable custom integrations in all tests."""
    return


@pytest.fixture(autouse=True)
async def setup_http(hass):
    """Load the http component (frontend/resources registration needs it)."""
    assert await async_setup_component(hass, "http", {})
    yield
