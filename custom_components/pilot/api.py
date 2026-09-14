"""Async client for the Pilot runtime HTTP API."""

from __future__ import annotations

from typing import Any, cast

from aiohttp import ClientError, ClientResponseError, ClientSession, ClientTimeout

from .const import REQUEST_TIMEOUT, CannotConnect, PilotApiError

API_PREFIX = "/api"


class PilotApiClient:
    """Minimal HTTP client for the Pilot runtime (add-on or remote instance)."""

    def __init__(
        self,
        host: str,
        port: int,
        token: str,
        session: ClientSession,
    ) -> None:
        self._host = host
        self._port = port
        self._token = token
        self._session = session

    @property
    def base_url(self) -> str:
        """Return the runtime base URL."""
        return f"http://{self._host}:{self._port}"

    async def async_validate(self) -> None:
        """Validate connectivity and credentials; raise CannotConnect on failure."""
        await self._request("GET", f"{API_PREFIX}/validate")

    async def async_get_status(self) -> dict[str, Any]:
        """Fetch the runtime status snapshot.

        Returns a dict with keys: status, vitrine_age_s, cost_today,
        queue_size, awaiting_confirmation, runtime_version, persona
        (butler_observer/politeness/verbosity/conservative, 0-100),
        daily_budget, persona_preset, mode, current_focus.
        """
        return await self._request("GET", f"{API_PREFIX}/status")

    async def async_set_persona(self, slider: str, value: int) -> None:
        """Set one persona slider (the four PERSONA_SLIDERS keys)."""
        await self._request(
            "POST", f"{API_PREFIX}/persona", json={"slider": slider, "value": value}
        )

    async def async_set_daily_budget(self, value: float) -> None:
        """Set the hard daily LLM budget in currency units."""
        await self._request("POST", f"{API_PREFIX}/budget", json={"value": value})

    async def async_set_persona_preset(self, preset: str) -> None:
        """Apply a named persona preset (butler/observer/economy)."""
        await self._request("POST", f"{API_PREFIX}/preset", json={"preset": preset})

    async def async_set_mode(self, mode: str) -> None:
        """Set the home mode (normal/vacation/guests/sick)."""
        await self._request("POST", f"{API_PREFIX}/mode", json={"mode": mode})

    async def async_set_current_focus(self, focus: str) -> None:
        """Set the agent's current focus note."""
        await self._request("POST", f"{API_PREFIX}/focus", json={"focus": focus})

    async def async_reset(self, target: str) -> None:
        """Reset learning or everything (target: learning | all)."""
        await self._request("POST", f"{API_PREFIX}/reset", json={"target": target})

    async def async_get_queue(self) -> list[dict[str, Any]]:
        """Fetch the confirmation queue (trust loop)."""
        data = await self._request("GET", f"{API_PREFIX}/queue")
        return cast(list[dict[str, Any]], data.get("items", []))

    async def async_confirm(self, item_id: str, decision: str) -> None:
        """Approve or reject a queue item (decision: yes | no)."""
        await self._request(
            "POST",
            f"{API_PREFIX}/queue/confirm",
            json={"id": item_id, "decision": decision},
        )

    async def async_get_vitrine(self) -> dict[str, Any]:
        """Fetch the home data vitrine (snapshot text lines + freshness)."""
        return await self._request("GET", f"{API_PREFIX}/vitrine")

    async def _request(
        self, method: str, path: str, json: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else None
        try:
            async with self._session.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
                json=json,
                timeout=ClientTimeout(total=REQUEST_TIMEOUT),
            ) as resp:
                resp.raise_for_status()
                if resp.status == 204:
                    return {}
                return cast(dict[str, Any], await resp.json())
        except ClientResponseError as err:
            if err.status == 401:
                raise CannotConnect("invalid token") from err
            raise PilotApiError(f"HTTP {err.status} from runtime") from err
        except (TimeoutError, ClientError) as err:
            raise CannotConnect(str(err)) from err
