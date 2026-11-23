"""Update platform for Stuffy Meter firmware OTA updates."""

from __future__ import annotations

import asyncio
import base64
import logging
import time
from typing import Any

from homeassistant.components.update import (
    UpdateDeviceClass,
    UpdateEntity,
    UpdateEntityFeature,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import (
    StuffyMeterApiError,
    StuffyMeterAuthError,
    StuffyMeterConnectionError,
    StuffyMeterError,
)
from .const import (
    DOMAIN,
    OTA_INSTALL_POLL_INTERVAL,
    OTA_INSTALL_START_GRACE,
    OTA_INSTALL_TIMEOUT,
    OTA_REBOOT_TIMEOUT,
)
from .coordinator import StuffyMeterCoordinator
from .entity import StuffyMeterEntity

_LOGGER = logging.getLogger(__name__)

ACTIVE_OTA_STATES = frozenset({"receiving", "downloading", "verifying", "success"})


def _svg_data_uri(svg: str) -> str:
    """Encode an inline SVG string as a base64 data URI for HA state-badge."""
    encoded = base64.b64encode(svg.encode("utf-8")).decode("ascii")
    return f"data:image/svg+xml;base64,{encoded}"


# Rounded-square update badges rendered by Home Assistant's state-badge
UPDATE_PICTURE_AVAILABLE = _svg_data_uri(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0%" stop-color="#fbbf24"/>'
    '<stop offset="100%" stop-color="#ea580c"/>'
    "</linearGradient></defs>"
    '<rect x="2" y="2" width="60" height="60" rx="14" fill="url(#g)"/>'
    '<path d="M32 18v21M22 28l10-10 10 10M20 45h24" fill="none" '
    'stroke="#fff" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"/>'
    '<circle cx="51" cy="13" r="7" fill="#ef4444" stroke="#fff" stroke-width="2.5"/>'
    "</svg>"
)

UPDATE_PICTURE_INSTALLING = _svg_data_uri(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0%" stop-color="#38bdf8"/>'
    '<stop offset="100%" stop-color="#2563eb"/>'
    "</linearGradient></defs>"
    '<rect x="2" y="2" width="60" height="60" rx="14" fill="url(#g)"/>'
    '<path d="M32 17v21M22 29l10 10 10-10M20 46h24" fill="none" '
    'stroke="#fff" stroke-width="4.5" stroke-linecap="round" stroke-linejoin="round"/>'
    "</svg>"
)

UPDATE_PICTURE_UP_TO_DATE = _svg_data_uri(
    '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">'
    '<defs><linearGradient id="g" x1="0" y1="0" x2="1" y2="1">'
    '<stop offset="0%" stop-color="#334155"/>'
    '<stop offset="100%" stop-color="#1e293b"/>'
    "</linearGradient></defs>"
    '<rect x="2" y="2" width="60" height="60" rx="14" fill="url(#g)"/>'
    '<circle cx="32" cy="32" r="16" fill="none" stroke="#34d399" stroke-width="3.5"/>'
    '<path d="M24.5 32.5l5 5 10.5-11" fill="none" stroke="#34d399" '
    'stroke-width="4" stroke-linecap="round" stroke-linejoin="round"/>'
    "</svg>"
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up the Stuffy Meter firmware update entity."""
    coordinator: StuffyMeterCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities([StuffyMeterFirmwareUpdate(coordinator)])


class StuffyMeterFirmwareUpdate(StuffyMeterEntity, UpdateEntity):
    """Representation of a Stuffy Meter firmware OTA update entity."""

    _attr_device_class = UpdateDeviceClass.FIRMWARE
    _attr_entity_category = EntityCategory.CONFIG
    _attr_supported_features = (
        UpdateEntityFeature.INSTALL | UpdateEntityFeature.PROGRESS
    )

    def __init__(self, coordinator: StuffyMeterCoordinator) -> None:
        super().__init__(coordinator, "firmware")
        self._install_in_progress = False
        self._install_percentage: int | None = None
        self._install_target: str | None = None

    def _ota_status(self) -> dict[str, Any]:
        if not isinstance(self.coordinator.data, dict):
            return {}
        ota = self.coordinator.data.get("ota")
        return ota if isinstance(ota, dict) else {}

    def _ota_auto(self) -> dict[str, Any]:
        if not isinstance(self.coordinator.ota_info, dict):
            return {}
        auto = self.coordinator.ota_info.get("auto")
        return auto if isinstance(auto, dict) else {}

    def _has_available_update(self) -> bool:
        """Return True when the monitor reports a newer available_version."""
        available = self._ota_status().get("available_version")
        if not isinstance(available, str) or not available.strip():
            return False
        installed = self.installed_version
        return installed is None or available.strip() != installed

    @property
    def entity_picture(self) -> str | None:
        """Show a dedicated color-highlighted update badge instead of the brand logo."""
        if self.in_progress:
            return UPDATE_PICTURE_INSTALLING
        if self._has_available_update():
            return UPDATE_PICTURE_AVAILABLE
        return UPDATE_PICTURE_UP_TO_DATE

    @property
    def icon(self) -> str:
        """Fallback MDI icon for cards that override entity_picture."""
        if self.in_progress:
            return "mdi:progress-download"
        if self._has_available_update():
            return "mdi:package-up"
        return "mdi:package-check"

    @property
    def available(self) -> bool:
        """Keep the update entity interactive across the brief OTA reboot window."""
        return self._install_in_progress or super().available

    @property
    def installed_version(self) -> str | None:
        """Return the currently running firmware version."""
        if not isinstance(self.coordinator.data, dict):
            return None
        device = self.coordinator.data.get("device")
        if not isinstance(device, dict):
            return None
        fw = device.get("fw_version")
        return str(fw).strip() if isinstance(fw, str) and fw.strip() else None

    @property
    def latest_version(self) -> str | None:
        """Return the newest available firmware version reported by the monitor."""
        ota = self._ota_status()
        available = ota.get("available_version")
        if isinstance(available, str) and available.strip():
            return available.strip()

        if self.in_progress:
            new_ver = ota.get("new_version")
            if isinstance(new_ver, str) and new_ver.strip():
                return new_ver.strip()
            if self._install_target:
                return self._install_target

        return self.installed_version

    @property
    def release_summary(self) -> str | None:
        """Return a short update summary displayed in the update dialog."""
        if self._has_available_update() and self.latest_version:
            if self.installed_version:
                return (
                    f"{self.latest_version} is available for update "
                    f"(installed: {self.installed_version})."
                )
            return f"{self.latest_version} is available for update."
        return None

    @property
    def release_url(self) -> str | None:
        """Return the GitHub release notes URL from GET /api/ota when available."""
        notes_url = self._ota_auto().get("notes_url")
        if isinstance(notes_url, str):
            url = notes_url.strip()
            if url.startswith(("https://", "http://")):
                return url
        return None

    @property
    def in_progress(self) -> bool:
        """Return True while an OTA update is downloading, verifying, or rebooting."""
        if self._install_in_progress:
            return True
        if not super().available:
            return False
        state = self._ota_status().get("state")
        return isinstance(state, str) and state in ACTIVE_OTA_STATES

    @property
    def update_percentage(self) -> int | None:
        """Return OTA download/install progress (0-100) while in_progress is True."""
        if not self.in_progress:
            return None
        if self._install_in_progress:
            return self._install_percentage
        progress = self._ota_status().get("progress")
        if isinstance(progress, (int, float)):
            return max(0, min(100, int(progress)))
        return None

    async def async_update(self) -> None:
        """Check for firmware updates on demand (called by homeassistant.update_entity)."""
        if self._install_in_progress:
            return
        try:
            await self.coordinator.async_check_for_updates()
        except StuffyMeterAuthError:
            _LOGGER.debug(
                "Skipping active /api/ota/check on %s (admin password required); "
                "refreshing cached OTA status instead",
                self.coordinator.client.host,
            )
            await self.coordinator.async_refresh_ota_info()
            await self.coordinator.async_request_refresh()
        except StuffyMeterApiError as err:
            if err.status == 409:
                await self.coordinator.async_refresh_ota_info()
                await self.coordinator.async_request_refresh()
                return
            _LOGGER.warning(
                "Firmware update check failed on %s: %s",
                self.coordinator.client.host,
                err,
            )
            await self.coordinator.async_request_refresh()
        except StuffyMeterError as err:
            _LOGGER.warning(
                "Firmware update check failed on %s: %s",
                self.coordinator.client.host,
                err,
            )
            await self.coordinator.async_request_refresh()

    async def async_install(
        self,
        version: str | None,
        backup: bool,
        **kwargs: Any,
    ) -> None:
        """Install the latest firmware via POST /api/ota/check and track progress."""
        del backup, kwargs
        status_before = (
            self.coordinator.data
            if isinstance(self.coordinator.data, dict)
            else {}
        )
        device_before = status_before.get("device") or {}
        before_version = self.installed_version or ""
        before_boot = device_before.get("boot_count")
        before_uptime = device_before.get("uptime_s")

        self._install_in_progress = True
        self._install_percentage = 0
        self._install_target = version or self.latest_version
        self.async_write_ha_state()

        try:
            try:
                res = await self.coordinator.client.async_check_ota(install=True)
            except StuffyMeterAuthError as err:
                raise HomeAssistantError(
                    "Admin password is required for firmware updates; "
                    "set it in Stuffy Meter integration options."
                ) from err
            except StuffyMeterError as err:
                raise HomeAssistantError(str(err)) from err

            if not res.get("update"):
                await self.coordinator.async_refresh_ota_info()
                try:
                    fresh = await self.coordinator.client.async_get_status()
                except StuffyMeterError:
                    await self.coordinator.async_request_refresh()
                else:
                    self._install_in_progress = False
                    self._install_percentage = None
                    self._install_target = None
                    self.coordinator.async_apply_status(fresh)
                return

            if not res.get("installing"):
                await self.coordinator.async_refresh_ota_info()
                raise HomeAssistantError(
                    "Firmware update did not start on the monitor."
                )

            available = res.get("available")
            if isinstance(available, str) and available.strip():
                self._install_target = available.strip()

            await self._async_wait_for_install(
                before_version=before_version,
                before_boot=before_boot,
                before_uptime=before_uptime,
            )
        finally:
            self._install_in_progress = False
            self._install_percentage = None
            self._install_target = None
            self.async_write_ha_state()

    async def _async_wait_for_install(
        self,
        *,
        before_version: str,
        before_boot: Any,
        before_uptime: Any,
    ) -> None:
        """Poll GET /api/status until the new firmware boots or fails."""
        started = time.monotonic()
        deadline = started + OTA_INSTALL_TIMEOUT
        seen_active = False
        seen_disconnect = False
        disconnect_since: float | None = None
        last_uptime = before_uptime

        while time.monotonic() < deadline:
            await asyncio.sleep(OTA_INSTALL_POLL_INTERVAL)
            now = time.monotonic()

            try:
                status = await self.coordinator.client.async_get_status()
            except StuffyMeterConnectionError as err:
                seen_disconnect = True
                if disconnect_since is None:
                    disconnect_since = now
                if now - disconnect_since >= OTA_REBOOT_TIMEOUT:
                    if hasattr(self.coordinator, "async_set_update_error"):
                        self.coordinator.async_set_update_error(err)
                    else:  # pragma: no cover
                        self.coordinator.last_update_success = False
                    if seen_active:
                        raise HomeAssistantError(
                            "Monitor did not come back online after firmware update reboot."
                        ) from err
                    raise HomeAssistantError(
                        "Lost connection to the monitor during firmware update."
                    ) from err
                continue
            except StuffyMeterError:
                continue

            disconnect_since = None
            device = status.get("device") or {}
            ota = status.get("ota") or {}
            fw_version = str(device.get("fw_version") or "").strip()
            boot_count = device.get("boot_count")
            uptime_s = device.get("uptime_s")

            fw_changed = bool(
                fw_version and before_version and fw_version != before_version
            )
            boot_changed = (
                boot_count is not None
                and before_boot is not None
                and boot_count != before_boot
            )
            uptime_reset = (
                seen_disconnect
                and isinstance(uptime_s, (int, float))
                and isinstance(last_uptime, (int, float))
                and uptime_s < last_uptime
            )

            if fw_changed:
                self._install_in_progress = False
                self._install_percentage = None
                self._install_target = None
                await self.coordinator.async_refresh_ota_info(status)
                self.coordinator.async_apply_status(status)
                return

            if boot_changed or uptime_reset:
                self._install_in_progress = False
                self._install_percentage = None
                self._install_target = None
                await self.coordinator.async_refresh_ota_info(status)
                self.coordinator.async_apply_status(status)
                last_err = str(ota.get("last_error") or "").strip()
                detail = f": {last_err}" if last_err else ""
                raise HomeAssistantError(
                    f"Firmware update rolled back to {fw_version or before_version}{detail}"
                )

            if isinstance(uptime_s, (int, float)):
                last_uptime = uptime_s

            state = str(ota.get("state") or "")
            if state in ("receiving", "downloading", "verifying"):
                seen_active = True
                progress = ota.get("progress")
                if isinstance(progress, (int, float)):
                    self._install_percentage = max(0, min(100, int(progress)))
                self.coordinator.async_apply_status(status)
            elif state == "success":
                seen_active = True
                self._install_percentage = 100
                self.coordinator.async_apply_status(status)
            elif state == "failed":
                self._install_in_progress = False
                self._install_percentage = None
                self._install_target = None
                await self.coordinator.async_refresh_ota_info(status)
                self.coordinator.async_apply_status(status)
                last_err = str(ota.get("last_error") or "").strip()
                raise HomeAssistantError(
                    f"Firmware update failed: {last_err or 'unknown error'}"
                )
            elif not seen_active and (now - started) >= OTA_INSTALL_START_GRACE:
                last_err = str(ota.get("last_error") or "").strip()
                raise HomeAssistantError(
                    f"Firmware update did not start: {last_err or 'monitor remained idle'}"
                )
            elif seen_active and (now - started) >= OTA_INSTALL_START_GRACE:
                last_err = str(ota.get("last_error") or "").strip()
                raise HomeAssistantError(
                    f"Firmware update stopped unexpectedly: {last_err or 'monitor returned to idle'}"
                )

        raise HomeAssistantError(
            "Timed out waiting for firmware update to complete."
        )
