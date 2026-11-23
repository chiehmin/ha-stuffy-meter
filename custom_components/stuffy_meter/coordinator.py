"""DataUpdateCoordinator for Stuffy Meter."""

from __future__ import annotations

from datetime import timedelta
import logging
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import StuffyMeterClient, StuffyMeterError
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    OTA_INFO_REFRESH_INTERVAL,
    OTA_INFO_RETRY_INTERVAL,
)

_LOGGER = logging.getLogger(__name__)


def extract_device_name(data: dict[str, Any]) -> str:
    """Return the user-friendly monitor name from a /api/status payload."""
    device = data.get("device") or {}
    friendly = (device.get("name") or "").strip()
    if friendly:
        return friendly
    dev_id = device.get("id") or ""
    return f"Stuffy Meter {dev_id}".strip()


def extract_model_name(data: dict[str, Any]) -> str:
    """Return a readable hardware model string from /api/status."""
    device = data.get("device") or {}
    board = str(device.get("board") or device.get("hw_rev") or "").strip()
    if board.startswith("4"):
        return f"Stuffy Meter Battery (rev {board})"
    if board:
        return f"Stuffy Meter USB (rev {board})"
    return str(device.get("model") or "Stuffy Meter")


def _ota_version_key(data: dict[str, Any]) -> tuple[str, str]:
    """Return (fw_version, available_version) from a /api/status payload."""
    device = data.get("device") or {}
    ota = data.get("ota") or {}
    return (
        str(device.get("fw_version") or ""),
        str(ota.get("available_version") or ""),
    )


class StuffyMeterCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Polls GET /api/status from a single Stuffy Meter on the LAN."""

    def __init__(
        self,
        hass: HomeAssistant,
        entry: ConfigEntry,
        client: StuffyMeterClient,
    ) -> None:
        self.client = client
        self.entry = entry
        scan_interval = entry.options.get(
            CONF_SCAN_INTERVAL,
            entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
        super().__init__(
            hass,
            _LOGGER,
            config_entry=entry,
            name=f"{DOMAIN}_{entry.unique_id or entry.data[CONF_HOST]}",
            update_interval=timedelta(seconds=int(scan_interval)),
        )
        self._last_device_name: str | None = None
        self._last_sw_version: str | None = None
        self.ota_info: dict[str, Any] | None = None
        self._ota_info_fetched_at: float = 0.0
        self._last_ota_key: tuple[str, str] | None = None

    @property
    def device_id(self) -> str:
        """Return the stable 6-hex-character Stuffy Meter device ID."""
        if self.data:
            dev_id = (self.data.get("device") or {}).get("id")
            if dev_id:
                return str(dev_id)
        return str(self.entry.unique_id or self.client.host)

    @property
    def has_battery(self) -> bool:
        """Return True if this monitor reports a Battery edition power block."""
        if not self.data:
            return False
        return isinstance(self.data.get("power"), dict)

    def _should_refresh_ota_info(self, data: dict[str, Any]) -> bool:
        """Return True when GET /api/ota metadata should be refreshed."""
        if self._ota_info_fetched_at == 0.0:
            return True
        if _ota_version_key(data) != self._last_ota_key:
            return True
        auto = (
            self.ota_info.get("auto")
            if isinstance(self.ota_info, dict)
            else None
        )
        last_result = auto.get("last_result") if isinstance(auto, dict) else ""
        interval = (
            OTA_INFO_RETRY_INTERVAL
            if not last_result
            else OTA_INFO_REFRESH_INTERVAL
        )
        return (time.monotonic() - self._ota_info_fetched_at) >= interval

    async def async_refresh_ota_info(
        self,
        status_data: dict[str, Any] | None = None,
    ) -> dict[str, Any] | None:
        """Fetch GET /api/ota metadata without failing the main status poll."""
        self._ota_info_fetched_at = time.monotonic()
        ref = status_data if status_data is not None else self.data
        if isinstance(ref, dict):
            self._last_ota_key = _ota_version_key(ref)
        try:
            self.ota_info = await self.client.async_get_ota()
        except StuffyMeterError as err:
            _LOGGER.debug(
                "Could not fetch /api/ota from %s: %s",
                self.client.host,
                err,
            )
        return self.ota_info

    def async_apply_status(self, data: dict[str, Any]) -> None:
        """Sync device registry metadata and push fresh /api/status data to entities."""
        self._sync_device_registry(data)
        self.async_set_updated_data(data)

    async def async_check_for_updates(self) -> dict[str, Any]:
        """Trigger POST /api/ota/check (report only) and refresh cached OTA/status data."""
        result = await self.client.async_check_ota(install=False)
        await self.async_refresh_ota_info()
        try:
            status = await self.client.async_get_status()
        except StuffyMeterError:
            await self.async_request_refresh()
        else:
            self._last_ota_key = _ota_version_key(status)
            self.async_apply_status(status)
        return result

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch the latest status JSON from the monitor."""
        try:
            data = await self.client.async_get_status()
        except StuffyMeterError as err:
            raise UpdateFailed(str(err)) from err

        self._sync_device_registry(data)
        if self._should_refresh_ota_info(data):
            await self.async_refresh_ota_info(data)
        return data

    def _sync_device_registry(self, data: dict[str, Any]) -> None:
        """Keep HA's device name and firmware version in sync after renames/OTA."""
        device = data.get("device") or {}
        dev_id = device.get("id")
        if not dev_id:
            return

        new_name = extract_device_name(data)
        new_sw = device.get("fw_version")
        if (
            self._last_device_name is not None
            and new_name == self._last_device_name
            and new_sw == self._last_sw_version
        ):
            return

        self._last_device_name = new_name
        self._last_sw_version = new_sw

        dev_reg = dr.async_get(self.hass)
        if hasattr(dev_reg, "async_get_device_by_identifier"):
            dev_entry = dev_reg.async_get_device_by_identifier(
                (DOMAIN, str(dev_id)),
                self.entry.entry_id,
            )
        else:  # pragma: no cover - Home Assistant < 2026.8
            dev_entry = dev_reg.async_get_device(identifiers={(DOMAIN, str(dev_id))})
        if dev_entry is None:
            return

        kwargs: dict[str, Any] = {}
        if new_name and dev_entry.name != new_name:
            kwargs["name"] = new_name
        if new_sw and dev_entry.sw_version != new_sw:
            kwargs["sw_version"] = str(new_sw)
        if kwargs:
            dev_reg.async_update_device(dev_entry.id, **kwargs)
