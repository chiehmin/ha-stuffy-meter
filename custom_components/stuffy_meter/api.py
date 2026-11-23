"""Async HTTP client for the Stuffy Meter local REST API."""

from __future__ import annotations

import asyncio
import base64
from typing import Any
from urllib.parse import urlsplit

import aiohttp

from .const import ADMIN_USERNAME, API_TIMEOUT, IDENTIFY_SECONDS, OTA_CHECK_TIMEOUT


class StuffyMeterError(Exception):
    """Base error for Stuffy Meter API operations."""


class StuffyMeterConnectionError(StuffyMeterError):
    """Raised when the monitor cannot be reached."""


class StuffyMeterApiError(StuffyMeterConnectionError):
    """Raised when the monitor returns an HTTP >= 400 error response."""

    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status = status


class StuffyMeterAuthError(StuffyMeterError):
    """Raised when admin authentication fails (HTTP 401/403)."""


class StuffyMeterInvalidResponseError(StuffyMeterError):
    """Raised when the endpoint does not return a valid Stuffy Meter payload."""


def normalize_host(host: str) -> str:
    """Strip scheme, path, query, and trailing slash from a user-provided host or IP."""
    cleaned = host.strip()
    if "://" in cleaned:
        parsed = urlsplit(cleaned)
        if parsed.netloc:
            return parsed.netloc
    return cleaned.split("/", 1)[0].strip()


class StuffyMeterClient:
    """Client for talking to a single Stuffy Meter over local HTTP."""

    def __init__(
        self,
        session: aiohttp.ClientSession,
        host: str,
        password: str | None = None,
        timeout: int = API_TIMEOUT,
    ) -> None:
        self._session = session
        self._host = normalize_host(host)
        self._password = password or ""
        self._timeout = aiohttp.ClientTimeout(total=timeout)

    @property
    def host(self) -> str:
        """Return the normalized host or IP."""
        return self._host

    @property
    def base_url(self) -> str:
        """Return the base HTTP URL for the monitor."""
        return f"http://{self._host}"

    def _auth_header(self) -> str | None:
        if not self._password:
            return None
        token = base64.b64encode(
            f"{ADMIN_USERNAME}:{self._password}".encode("utf-8")
        ).decode("ascii")
        return f"Basic {token}"

    async def _request_json(
        self,
        method: str,
        path: str,
        *,
        json_body: dict[str, Any] | None = None,
        use_auth: bool = False,
        timeout: int | None = None,
    ) -> dict[str, Any]:
        url = f"{self.base_url}{path}"
        headers: dict[str, str] = {}
        if method == "POST":
            headers["Content-Type"] = "application/json"
        if use_auth:
            auth_hdr = self._auth_header()
            if auth_hdr:
                headers["Authorization"] = auth_hdr
        req_timeout = (
            aiohttp.ClientTimeout(total=timeout)
            if timeout is not None
            else self._timeout
        )

        try:
            async with self._session.request(
                method,
                url,
                json=json_body if method == "POST" else None,
                headers=headers or None,
                timeout=req_timeout,
            ) as resp:
                if resp.status in (401, 403):
                    raise StuffyMeterAuthError(
                        f"Authentication failed for {path} (HTTP {resp.status})"
                    )
                if resp.status >= 400:
                    err_msg = ""
                    try:
                        err_body = await resp.json(content_type=None)
                        if isinstance(err_body, dict) and isinstance(
                            err_body.get("error"), str
                        ):
                            err_msg = err_body["error"].strip()
                    except (ValueError, aiohttp.ContentTypeError):
                        pass
                    raise StuffyMeterApiError(
                        resp.status,
                        err_msg or f"Unexpected HTTP {resp.status} from {url}",
                    )
                try:
                    data = await resp.json(content_type=None)
                except (ValueError, aiohttp.ContentTypeError) as err:
                    raise StuffyMeterInvalidResponseError(
                        f"Invalid JSON returned from {url}"
                    ) from err
        except (aiohttp.ClientError, asyncio.TimeoutError) as err:
            raise StuffyMeterConnectionError(
                f"Cannot reach Stuffy Meter at {self._host}: {err}"
            ) from err

        if not isinstance(data, dict):
            raise StuffyMeterInvalidResponseError(
                f"Expected JSON object from {url}"
            )
        return data

    async def async_get_status(self) -> dict[str, Any]:
        """Fetch GET /api/status and validate it is a Stuffy Meter."""
        data = await self._request_json("GET", "/api/status")
        device = data.get("device")
        readings = data.get("readings")
        if (
            not isinstance(device, dict)
            or not device.get("id")
            or not isinstance(readings, dict)
        ):
            raise StuffyMeterInvalidResponseError(
                f"Host {self._host} did not return a Stuffy Meter /api/status payload"
            )
        return data

    async def async_verify_auth(self) -> dict[str, Any]:
        """Verify admin credentials via GET /api/config."""
        return await self._request_json("GET", "/api/config", use_auth=True)

    async def async_identify(self, seconds: int = IDENTIFY_SECONDS) -> dict[str, Any]:
        """Blink the monitor LED and flash its screen (public endpoint)."""
        return await self._request_json(
            "POST",
            "/api/identify",
            json_body={"seconds": seconds},
        )

    async def async_reboot(self) -> dict[str, Any]:
        """Reboot the monitor (requires admin password when auth is enabled)."""
        return await self._request_json(
            "POST",
            "/api/reboot",
            json_body={},
            use_auth=True,
        )

    async def async_get_ota(self) -> dict[str, Any]:
        """Fetch GET /api/ota (public endpoint: slots, auto-update status, notes URL)."""
        return await self._request_json("GET", "/api/ota")

    async def async_check_ota(self, *, install: bool = False) -> dict[str, Any]:
        """Check the configured OTA manifest and optionally start installing (admin endpoint)."""
        return await self._request_json(
            "POST",
            "/api/ota/check",
            json_body={"install": install},
            use_auth=True,
            timeout=OTA_CHECK_TIMEOUT,
        )
