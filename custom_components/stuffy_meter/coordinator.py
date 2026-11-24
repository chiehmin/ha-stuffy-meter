"""DataUpdateCoordinator for Stuffy Meter."""

from __future__ import annotations

from datetime import datetime, timedelta
import logging
import math
import time
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_HOST
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed
from homeassistant.util import dt as dt_util

from .api import StuffyMeterAuthError, StuffyMeterClient, StuffyMeterError
from .const import (
    CONF_SCAN_INTERVAL,
    CONFIG_REFRESH_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    HISTORY_1H_REFRESH_INTERVAL,
    HISTORY_7D_REFRESH_INTERVAL,
    INSIGHTS_RETRY_INTERVAL,
    OTA_INFO_REFRESH_INTERVAL,
    OTA_INFO_RETRY_INTERVAL,
)
from .insights import compute_insights

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
        self._boot_time: datetime | None = None
        self._last_boot_count: Any = None
        self._last_uptime_s: float | None = None
        self._reauth_triggered: bool = False
        self.ota_info: dict[str, Any] | None = None
        self._ota_info_fetched_at: float = 0.0
        self._last_ota_key: tuple[str, str] | None = None
        self.history_1h: dict[str, Any] | None = None
        self._history_1h_fetched_at: float = 0.0
        self._history_1h_interval: float = HISTORY_1H_REFRESH_INTERVAL
        self.history_7d: dict[str, Any] | None = None
        self._history_7d_fetched_at: float = 0.0
        self._history_7d_interval: float = HISTORY_7D_REFRESH_INTERVAL
        self.config_data: dict[str, Any] | None = None
        self._config_fetched_at: float = 0.0
        self._config_interval: float = CONFIG_REFRESH_INTERVAL
        self.insights: dict[str, Any] = {"vent": None, "comfort": None, "week": None}

    def get_boot_time(self) -> datetime | None:
        """Return a stable UTC boot timestamp, updating only on reboot or major drift."""
        if not isinstance(self.data, dict):
            return None
        device = self.data.get("device")
        if not isinstance(device, dict):
            return None
        raw_uptime = device.get("uptime_s")
        if (
            not isinstance(raw_uptime, (int, float))
            or isinstance(raw_uptime, bool)
            or not math.isfinite(raw_uptime)
            or raw_uptime < 0
        ):
            return None

        uptime_s = float(raw_uptime)
        boot_count = device.get("boot_count")
        estimated = (
            dt_util.utcnow() - timedelta(seconds=uptime_s)
        ).replace(microsecond=0)

        rebooted = (
            self._boot_time is None
            or (
                boot_count is not None
                and self._last_boot_count is not None
                and boot_count != self._last_boot_count
            )
            or (self._last_uptime_s is not None and uptime_s < self._last_uptime_s)
            or abs((estimated - self._boot_time).total_seconds()) > 60.0
        )
        if rebooted:
            self._boot_time = estimated
        if boot_count is not None:
            self._last_boot_count = boot_count
        self._last_uptime_s = uptime_s
        return self._boot_time

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

    async def _async_refresh_insights_sources(self, data: dict[str, Any]) -> None:
        """Refresh cached 1h/7d history and optional threshold config without failing status."""
        now = time.monotonic()
        get_hist = getattr(self.client, "async_get_history", None)
        if callable(get_hist):
            if (
                self._history_1h_fetched_at == 0.0
                or (now - self._history_1h_fetched_at) >= self._history_1h_interval
            ):
                try:
                    self.history_1h = await get_hist(minutes=60, step=1)
                except StuffyMeterError as err:
                    self._history_1h_fetched_at = time.monotonic()
                    self._history_1h_interval = INSIGHTS_RETRY_INTERVAL
                    _LOGGER.debug(
                        "Could not fetch 1h /api/history from %s: %s",
                        self.client.host,
                        err,
                    )
                else:
                    self._history_1h_fetched_at = time.monotonic()
                    self._history_1h_interval = HISTORY_1H_REFRESH_INTERVAL
            if (
                self._history_7d_fetched_at == 0.0
                or (now - self._history_7d_fetched_at) >= self._history_7d_interval
            ):
                try:
                    self.history_7d = await get_hist(minutes=10080, step=30)
                except StuffyMeterError as err:
                    self._history_7d_fetched_at = time.monotonic()
                    self._history_7d_interval = INSIGHTS_RETRY_INTERVAL
                    _LOGGER.debug(
                        "Could not fetch 7d /api/history from %s: %s",
                        self.client.host,
                        err,
                    )
                else:
                    self._history_7d_fetched_at = time.monotonic()
                    self._history_7d_interval = HISTORY_7D_REFRESH_INTERVAL
        get_cfg = getattr(self.client, "async_get_config", None)
        device = data.get("device") if isinstance(data, dict) else None
        auth_open = isinstance(device, dict) and device.get("auth") is False
        can_read_cfg = auth_open or bool(getattr(self.client, "has_password", False))
        if callable(get_cfg) and can_read_cfg and (
            self._config_fetched_at == 0.0
            or (now - self._config_fetched_at) >= self._config_interval
        ):
            try:
                self.config_data = await get_cfg()
            except StuffyMeterAuthError as err:
                self._config_fetched_at = time.monotonic()
                self._config_interval = INSIGHTS_RETRY_INTERVAL
                if (
                    bool(getattr(self.client, "has_password", False))
                    and not self._reauth_triggered
                    and hasattr(self.entry, "async_start_reauth")
                ):
                    self._reauth_triggered = True
                    self.entry.async_start_reauth(self.hass)
                _LOGGER.debug(
                    "Admin auth rejected fetching /api/config thresholds from %s: %s",
                    self.client.host,
                    err,
                )
            except StuffyMeterError as err:
                self._config_fetched_at = time.monotonic()
                self._config_interval = INSIGHTS_RETRY_INTERVAL
                _LOGGER.debug(
                    "Could not fetch /api/config thresholds from %s: %s",
                    self.client.host,
                    err,
                )
            else:
                self._reauth_triggered = False
                self._config_fetched_at = time.monotonic()
                self._config_interval = CONFIG_REFRESH_INTERVAL

    def _recompute_insights(self, data: dict[str, Any]) -> None:
        self.insights = compute_insights(
            data,
            self.history_1h,
            self.history_7d,
            self.config_data,
            tz=dt_util.get_default_time_zone(),
        )

    def async_apply_status(self, data: dict[str, Any]) -> None:
        """Sync device registry metadata and push fresh /api/status data to entities."""
        self._sync_device_registry(data)
        self._recompute_insights(data)
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
        await self._async_refresh_insights_sources(data)
        self._recompute_insights(data)
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

        self._last_device_name = new_name
        self._last_sw_version = new_sw

        kwargs: dict[str, Any] = {}
        if new_name and dev_entry.name != new_name:
            kwargs["name"] = new_name
        if new_sw and dev_entry.sw_version != new_sw:
            kwargs["sw_version"] = str(new_sw)
        if kwargs:
            dev_reg.async_update_device(dev_entry.id, **kwargs)


type StuffyMeterConfigEntry = ConfigEntry[StuffyMeterCoordinator]
