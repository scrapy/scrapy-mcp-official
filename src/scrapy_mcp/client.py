from __future__ import annotations

import asyncio
from typing import Any, cast

import aiohttp

# used for the /execute request timeout when timeout_sec is empty
EXECUTE_TIMEOUT_DEFAULT = 600.0
# added to timeout_sec to get the total timeout for the /execute request
EXECUTE_DEADLINE_MARGIN = 30.0
# used for the /status request timeout
STATUS_TIMEOUT = 3.0


def _get_execute_deadline(timeout_sec: float | None) -> float:
    """Return the timeout to use for the execute request, given the user-specified timeout."""
    if timeout_sec is None:
        return EXECUTE_TIMEOUT_DEFAULT + EXECUTE_DEADLINE_MARGIN
    return timeout_sec + EXECUTE_DEADLINE_MARGIN


class RequestError(Exception):
    """Error making a request to a RemoteControl endpoint."""


class CrawlClient:
    """The remote-control endpoints of one crawl."""

    def __init__(self, base_url: str, token: str):
        self._base_url: str = base_url
        self._token: str = token
        self._headers: dict[str, str] = {"Authorization": f"Bearer {token}"}

    async def status(self) -> dict[str, Any]:
        """``GET /status`` — what the crawl is running, and proof that it can answer."""
        return await self._request("GET", "/status", timeout=STATUS_TIMEOUT)

    async def execute(
        self, code: str, timeout_sec: float | None = None
    ) -> dict[str, Any]:
        """``POST /execute`` — run ``code`` in the crawl, return its result envelope."""
        payload: dict[str, Any] = {"code": code}
        if timeout_sec is not None:
            payload["timeout_sec"] = timeout_sec
        deadline = _get_execute_deadline(timeout_sec)
        return await self._request("POST", "/execute", timeout=deadline, json=payload)

    async def _request(
        self,
        method: str,
        path: str,
        *,
        timeout: float,
        json: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Make one request, return the result."""
        client_timeout = aiohttp.ClientTimeout(total=timeout)
        try:
            async with (
                aiohttp.ClientSession(timeout=client_timeout) as session,
                session.request(
                    method,
                    self._base_url + path,
                    json=json,
                    headers=self._headers,
                ) as resp,
            ):
                if resp.status == 401:
                    raise RequestError("Request authentication error")
                if resp.status != 200:
                    body = (await resp.text())[:200]
                    raise RequestError(
                        f"Unexpected response status {resp.status}: {body}"
                    )
                return cast("dict[str, Any]", await resp.json())
        except aiohttp.ClientConnectorError as e:
            raise RequestError("Connection error") from e
        except asyncio.TimeoutError as e:
            raise RequestError(f"No response within {timeout} seconds") from e
        except aiohttp.ClientError as e:
            raise RequestError("Request error") from e
