"""Sensor platform for Stuffy Meter."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from homeassistant.components.sensor import (
    SensorDeviceClass,
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import (
    EntityCategory,
    PERCENTAGE,
    SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
    UnitOfElectricPotential,
    UnitOfInformation,
    UnitOfTemperature,
    UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

try:
    from homeassistant.const import UnitOfRatio

    PPM_UNIT: str = UnitOfRatio.PARTS_PER_MILLION
except ImportError:  # pragma: no cover - Home Assistant < 2026.8
    from homeassistant.const import CONCENTRATION_PARTS_PER_MILLION

    PPM_UNIT = CONCENTRATION_PARTS_PER_MILLION

from .const import DOMAIN
from .coordinator import StuffyMeterCoordinator
from .entity import StuffyMeterEntity

AIR_QUALITY_LEVELS = ["good", "moderate", "poor", "bad"]


@dataclass(frozen=True, kw_only=True)
class StuffyMeterSensorDescription(SensorEntityDescription):
    """Describe a Stuffy Meter sensor entity."""

    value_fn: Callable[[dict[str, Any]], Any]


def _get(data: dict[str, Any], section: str, field: str) -> Any:
    sub = data.get(section)
    if not isinstance(sub, dict):
        return None
    return sub.get(field)


def _air_quality_level(data: dict[str, Any]) -> str | None:
    level = _get(data, "readings", "level")
    if isinstance(level, str) and level in AIR_QUALITY_LEVELS:
        return level
    return None


def _battery_value(data: dict[str, Any], field: str) -> Any:
    power = data.get("power")
    if not isinstance(power, dict):
        return None
    if power.get("battery_present") is False:
        return None
    return power.get(field)


BASE_SENSORS: tuple[StuffyMeterSensorDescription, ...] = (
    StuffyMeterSensorDescription(
        key="co2",
        device_class=SensorDeviceClass.CO2,
        native_unit_of_measurement=PPM_UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: _get(d, "readings", "co2_ppm"),
    ),
    StuffyMeterSensorDescription(
        key="temperature",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: _get(d, "readings", "temperature_c"),
    ),
    StuffyMeterSensorDescription(
        key="humidity",
        device_class=SensorDeviceClass.HUMIDITY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: _get(d, "readings", "humidity_rh"),
    ),
    StuffyMeterSensorDescription(
        key="voc_index",
        translation_key="voc_index",
        icon="mdi:air-filter",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: _get(d, "readings", "voc_index"),
    ),
    StuffyMeterSensorDescription(
        key="dew_point",
        translation_key="dew_point",
        device_class=SensorDeviceClass.TEMPERATURE,
        native_unit_of_measurement=UnitOfTemperature.CELSIUS,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: _get(d, "readings", "dew_point_c"),
    ),
    StuffyMeterSensorDescription(
        key="abs_humidity",
        translation_key="abs_humidity",
        icon="mdi:water-percent",
        native_unit_of_measurement="g/m³",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=1,
        value_fn=lambda d: _get(d, "readings", "abs_humidity_gm3"),
    ),
    StuffyMeterSensorDescription(
        key="air_quality",
        translation_key="air_quality",
        device_class=SensorDeviceClass.ENUM,
        options=AIR_QUALITY_LEVELS,
        icon="mdi:air-filter",
        value_fn=_air_quality_level,
    ),
    StuffyMeterSensorDescription(
        key="rssi",
        translation_key="wifi_signal",
        device_class=SensorDeviceClass.SIGNAL_STRENGTH,
        native_unit_of_measurement=SIGNAL_STRENGTH_DECIBELS_MILLIWATT,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _get(d, "network", "rssi"),
    ),
    StuffyMeterSensorDescription(
        key="uptime",
        translation_key="uptime",
        device_class=SensorDeviceClass.DURATION,
        native_unit_of_measurement=UnitOfTime.SECONDS,
        state_class=SensorStateClass.TOTAL_INCREASING,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _get(d, "device", "uptime_s"),
    ),
    StuffyMeterSensorDescription(
        key="heap_free",
        translation_key="free_heap",
        icon="mdi:memory",
        device_class=SensorDeviceClass.DATA_SIZE,
        native_unit_of_measurement=UnitOfInformation.BYTES,
        state_class=SensorStateClass.MEASUREMENT,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _get(d, "device", "heap_free"),
    ),
)

BATTERY_SENSORS: tuple[StuffyMeterSensorDescription, ...] = (
    StuffyMeterSensorDescription(
        key="battery",
        device_class=SensorDeviceClass.BATTERY,
        native_unit_of_measurement=PERCENTAGE,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        value_fn=lambda d: _battery_value(d, "battery_pct"),
    ),
    StuffyMeterSensorDescription(
        key="battery_voltage",
        translation_key="battery_voltage",
        device_class=SensorDeviceClass.VOLTAGE,
        native_unit_of_measurement=UnitOfElectricPotential.VOLT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=2,
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=lambda d: _battery_value(d, "battery_v"),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Stuffy Meter sensor entities."""
    coordinator: StuffyMeterCoordinator = hass.data[DOMAIN][entry.entry_id]
    descriptions: list[StuffyMeterSensorDescription] = list(BASE_SENSORS)
    if coordinator.has_battery:
        descriptions.extend(BATTERY_SENSORS)
    async_add_entities(
        StuffyMeterSensor(coordinator, desc) for desc in descriptions
    )


class StuffyMeterSensor(StuffyMeterEntity, SensorEntity):
    """Representation of a Stuffy Meter sensor."""

    entity_description: StuffyMeterSensorDescription

    def __init__(
        self,
        coordinator: StuffyMeterCoordinator,
        description: StuffyMeterSensorDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self) -> Any:
        """Return the latest sensor value."""
        if not self.coordinator.data:
            return None
        return self.entity_description.value_fn(self.coordinator.data)
