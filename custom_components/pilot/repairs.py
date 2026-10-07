"""Repairs issues for the Pilot integration."""

from __future__ import annotations

from homeassistant.components.repairs import RepairsFlow
from homeassistant.core import HomeAssistant
from homeassistant.data_entry_flow import FlowResult
from homeassistant.helpers import issue_registry as ir

from .const import DOMAIN

ISSUE_RUNTIME_UNREACHABLE = "runtime_unreachable"
ISSUE_CORE_UPDATE_STUCK = "core_update_stuck"


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


class CoreUpdateStuckFlow(RepairsFlow):
    """Handler for the 'core migration needs attention' issue."""

    async def async_step_init(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Point at the workshop queue/status; clears when the gate resolves."""
        return self.async_show_form(step_id="confirm")

    async def async_step_confirm(
        self, user_input: dict[str, str] | None = None
    ) -> FlowResult:
        """Acknowledge; the issue is resolved automatically once migration completes."""
        return self.async_abort(reason="resolved_when_migration_done")


async def async_create_fix_flow(
    hass: HomeAssistant, issue_id: str, data: dict[str, str | int | float | None]
) -> RepairsFlow:
    """Create a repair flow for a Pilot issue."""
    if issue_id == ISSUE_CORE_UPDATE_STUCK:
        return CoreUpdateStuckFlow()
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


def async_sync_core_update_issue(
    hass: HomeAssistant, entry_id: str, core_update: dict[str, object] | None
) -> None:
    """Create or clear the core-migration issue from status.core_update.

    The add-on's migration gate (coregate.py) sets this field when a core
    version jump was snapshotted but the health check has not confirmed
    the migration yet — the owner should look before the next restart.
    """
    if core_update:
        ir.async_create_issue(
            hass,
            DOMAIN,
            ISSUE_CORE_UPDATE_STUCK,
            is_fixable=True,
            severity=ir.IssueSeverity.WARNING,
            translation_key=ISSUE_CORE_UPDATE_STUCK,
            translation_placeholders={
                "entry_id": entry_id,
                "target": str(core_update.get("to") or "?"),
                "snapshot": str(core_update.get("snapshot") or "—"),
            },
        )
    else:
        ir.async_delete_issue(hass, DOMAIN, ISSUE_CORE_UPDATE_STUCK)
