"""Repairs issues for the Pilot integration."""

from __future__ import annotations

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

ISSUE_RUNTIME_UNREACHABLE = "runtime_unreachable"


class RuntimeUnreachableFlow(RepairsFlow):
    """Handler for the 'runtime unreachable' issue."""

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Show issue details; the issue clears itself once the runtime is back."""
        return self.async_show_form(step_id="confirm")

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Acknowledge; the issue is resolved automatically by the integration."""
        return self.async_abort(reason="resolved_when_runtime_back")


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, str | int | float | None]
) -> RepairsFlow:
    """Create a repair flow for a Pilot issue."""
    return RuntimeUnreachableFlow()


def async_sync_repairs_issue(
    hass: HomeAssistant, entry_id: str, unreachable: bool
) -> None:
    """Create or clear the runtime-unreachable issue based on reachability."""
    if unreachable:
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_RUNTIME_UNREACHABLE,
            is_fixable=True,
            severity=ir.IssueSeverity.ERROR,
            translation_key=ISSUE_RUNTIME_UNREACHABLE,
            translation_placeholders={"entry_id": entry_id},
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_RUNTIME_UNREACHABLE)
