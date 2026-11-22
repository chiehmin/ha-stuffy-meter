"""Base entity for Stuffy Meter."""

from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_NETWORK_MAC, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN
from .coordinator import (
    StuffyMeterCoordinator,
    extract_device_name,
    extract_model_name,
)


class StuffyMeterEntity(CoordinatorEntity[StuffyMeterCoordinator]):
    """Shared device metadata and availability for Stuffy Meter entities."""

    _attr_has_entity_name = True

    def __init__(self, coordinator: StuffyMeterCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._attr_unique_id = f"{coordinator.device_id}_{key}"

    @property
    def device_info(self) -> DeviceInfo:
        """Return Home Assistant DeviceInfo built from /api/status."""
        data = self.coordinator.data or {}
        device = data.get("device") or {}
        network = data.get("network") or {}

        connections: set[tuple[str, str]] = set()
        mac = network.get("mac")
        if isinstance(mac, str) and mac.strip():
            connections.add((CONNECTION_NETWORK_MAC, mac.strip()))

        board = device.get("board") or device.get("hw_rev")
        return DeviceInfo(
            identifiers={(DOMAIN, self.coordinator.device_id)},
            connections=connections,
            name=extract_device_name(data),
            manufacturer=str(device.get("manufacturer") or "cmwang.dev"),
            model=extract_model_name(data),
            hw_version=str(board) if board else None,
            sw_version=str(device["fw_version"]) if device.get("fw_version") else None,
            configuration_url=f"{self.coordinator.client.base_url}/",
        )
