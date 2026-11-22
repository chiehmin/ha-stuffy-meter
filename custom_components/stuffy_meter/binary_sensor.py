"""Binary sensor platform for Stuffy Meter (Battery edition)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DOMAIN
from .coordinator import StuffyMeterCoordinator
from .entity import StuffyMeterEntity


@dataclass(frozen=True, kw_only=True)
class StuffyMeterBinarySensorDescription(BinarySensorEntityDescription):
    """Describe a Stuffy Meter binary sensor entity."""

    value_fn: Callable[[dict[str, Any]], bool | None]


def _power_bool(data: dict[str, Any], field: str) -> bool | None:
    power = data.get("power")
    if not isinstance(power, dict):
        return None
    val = power.get(field)
    return bool(val) if val is not None else None


BATTERY_BINARY_SENSORS: tuple[StuffyMeterBinarySensorDescription, ...] = (
    StuffyMeterBinarySensorDescription(
        key="power",
        translation_key="usb_power",
        device_class=BinarySensorDeviceClass.PLUG,
        value_fn=lambda d: _power_bool(d, "usb"),
    ),
    StuffyMeterBinarySensorDescription(
        key="battery_charging",
        device_class=BinarySensorDeviceClass.BATTERY_CHARGING,
        value_fn=lambda d: _power_bool(d, "charging"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Stuffy Meter binary sensors (Battery edition only)."""
    coordinator: StuffyMeterCoordinator = hass.data[DOMAIN][entry.entry_id]
    if not coordinator.has_battery:
        return
    async_add_entities(
        StuffyMeterBinarySensor(coordinator, desc)
        for desc in BATTERY_BINARY_SENSORS
    )


class StuffyMeterBinarySensor(StuffyMeterEntity, BinarySensorEntity):
    """Representation of a Stuffy Meter binary sensor."""

    entity_description: StuffyMeterBinarySensorDescription

    def __init__(
        self,
        coordinator: StuffyMeterCoordinator,
        description: StuffyMeterBinarySensorDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        """Return True if the binary sensor is active."""
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
