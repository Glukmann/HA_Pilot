"""Pilot as a Home Assistant conversation agent — the chat remote control.

The owner can pick Pilot as the (preferred) Assist agent and talk to the
home through it. Every turn is one LLM call at the add-on (external tokens,
the active model profile, the shared daily budget). The add-on classifies
each proposed action (safety.py): "direct" reversible actions are executed
here via hass.services, "queue" actions become trust-queue proposals the
owner confirms in the workshop, "refuse" actions are dropped with a note.
"""

from __future__ import annotations

import logging
from typing import Any, Literal

from homeassistant.components.conversation import (
    ConversationEntity,
    ConversationEntityFeature,
    ConversationInput,
    ConversationResult,
)
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ServiceNotFound, ServiceNotSupported
from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.intent import IntentResponse

from .const import DOMAIN, CannotConnect, PilotApiError
from .coordinator import PilotConfigEntry, PilotDataUpdateCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: PilotConfigEntry,
    async_add_entities: Any,
) -> None:
    """Register the Pilot conversation agent."""
    coordinator = entry.runtime_data
    async_add_entities([PilotConversationEntity(coordinator, entry)])


class PilotConversationEntity(ConversationEntity):
    """Answers Assist turns through the Pilot runtime."""

    _attr_has_entity_name = True
    _attr_name = "Pilot"
    _attr_supported_features = ConversationEntityFeature.CONTROL

    def __init__(
        self, coordinator: PilotDataUpdateCoordinator, entry: PilotConfigEntry
    ) -> None:
        self.coordinator = coordinator
        self._attr_unique_id = f"{entry.entry_id}_conversation"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, entry.entry_id)},
            name="Pilot Eyes",
            manufacturer="Pilot",
            model="Proactive home agent",
            entry_type=DeviceEntryType.SERVICE,
        )

    @property
    def supported_languages(self) -> list[str] | Literal["*"]:
        """Pilot chats in any language the owner's model does."""
        return "*"

    async def async_process(self, user_input: ConversationInput) -> ConversationResult:
        """One turn: ask the runtime, execute safe actions, enqueue the rest."""
        client = self.coordinator.api
        try:
            result = await client.async_ask(
                user_input.text,
                conversation_id=user_input.conversation_id,
                language=user_input.language,
            )
        except (CannotConnect, PilotApiError) as err:
            _LOGGER.debug("Pilot runtime unavailable: %s", err)
            return self._reply(user_input, "Пилот сейчас недоступен, попробуйте позже.")

        say = str(result.get("say") or "…")
        actions = [a for a in result.get("actions") or [] if isinstance(a, dict)]

        executed = 0
        executed_names: list[str] = []
        queued = 0
        refused = 0
        failed = 0
        for action in actions:
            mode = str(action.get("mode") or "queue")
            if mode == "refuse":
                refused += 1
                continue
            if mode == "queue":
                if await self._enqueue(action, client):
                    queued += 1
                continue
            if await self._execute(action):
                executed += 1
                executed_names.append(str(action.get("entity_id") or "действие"))
            else:
                failed += 1

        # Deterministic accounting: what actually happened, regardless of
        # what the LLM claimed in "say" — trust is built on facts.
        suffix: list[str] = []
        if executed:
            suffix.append(f"Выполнено: {', '.join(executed_names)}.")
        if queued:
            suffix.append("Ждёт вашего подтверждения в мастерской (Очередь).")
        if refused:
            suffix.append("Камеры и охрану из чата не трогаю.")
        if failed:
            suffix.append("Часть действий не удалась — подробности в журнале HA.")
        if executed and not say:
            say = "Готово."
        if suffix:
            say = " ".join([say, *suffix]).strip()

        return self._reply(user_input, say)

    async def _execute(self, action: dict[str, Any]) -> bool:
        """Execute one whitelisted action through HA services."""
        domain = str(action.get("domain") or "")
        service = str(action.get("service") or "")
        entity_id = str(action.get("entity_id") or "")
        data = {k: v for k, v in (action.get("data") or {}).items() if v is not None}
        data["entity_id"] = entity_id
        try:
            await self.hass.services.async_call(domain, service, data, blocking=True)
        except (ServiceNotFound, ServiceNotSupported, ValueError, TypeError) as err:
            _LOGGER.warning(
                "Pilot chat action failed: %s.%s %s: %s",
                domain,
                service,
                entity_id,
                err,
            )
            return False
        return True

    async def _enqueue(self, action: dict[str, Any], client: Any) -> bool:
        """Offer a non-whitelisted action to the owner via the trust queue."""
        domain = str(action.get("domain") or "")
        service = str(action.get("service") or "")
        entity_id = str(action.get("entity_id") or "")
        title = f"Чат: {domain}.{service} → {entity_id}"
        try:
            await client.async_enqueue(
                title=title,
                summary="Запрошено из чата Assist",
                action={
                    "type": "chat",
                    "kind": "service_call",
                    "domain": domain,
                    "service": service,
                    "entity_id": entity_id,
                    "data": action.get("data") or {},
                },
            )
        except (CannotConnect, PilotApiError) as err:
            _LOGGER.warning("Pilot chat enqueue failed: %s", err)
            return False
        return True

    def _reply(self, user_input: ConversationInput, text: str) -> ConversationResult:
        response = IntentResponse(
            language=user_input.language or self.hass.config.language
        )
        response.async_set_speech(text)
        return ConversationResult(
            response=response, conversation_id=user_input.conversation_id
        )
