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
        queue_size, runtime_version.
        """
        return await self._request("GET", f"{API_PREFIX}/status")

    async def _request(self, method: str, path: str) -> dict[str, Any]:
        headers = {"Authorization": f"Bearer {self._token}"} if self._token else None
        try:
            async with self._session.request(
                method,
                f"{self.base_url}{path}",
                headers=headers,
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
