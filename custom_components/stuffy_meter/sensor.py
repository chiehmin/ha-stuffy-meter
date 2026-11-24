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
from .insights import COMFORT_STATES, VENTILATION_STATES

AIR_QUALITY_LEVELS = ["good", "moderate", "poor", "bad"]

_VENT_ICONS: dict[str, str] = {
    "rising": "mdi:trending-up",
    "ventilating": "mdi:weather-windy",
    "high_voc": "mdi:alert-outline",
    "steady": "mdi:fan",
}

_COMFORT_ICONS: dict[str, str] = {
    "mold_risk": "mdi:water-alert",
    "muggy": "mdi:water-thermometer",
    "dry_air": "mdi:water-off",
    "warm": "mdi:thermometer-high",
    "cool": "mdi:thermometer-low",
    "comfortable": "mdi:home-thermometer-outline",
}


@dataclass(frozen=True, kw_only=True)
class StuffyMeterSensorDescription(SensorEntityDescription):
    """Describe a Stuffy Meter sensor entity."""

    value_fn: Callable[[dict[str, Any]], Any] | None = None
    coord_value_fn: Callable[[StuffyMeterCoordinator], Any] | None = None
    icon_fn: Callable[[dict[str, Any]], str | None] | None = None
    coord_icon_fn: Callable[[StuffyMeterCoordinator], str | None] | None = None
    extra_attrs_fn: Callable[[StuffyMeterCoordinator], dict[str, Any] | None] | None = None


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


def _firmware_versions(data: dict[str, Any]) -> tuple[str | None, str | None]:
    fw_raw = _get(data, "device", "fw_version")
    avail_raw = _get(data, "ota", "available_version")
    fw = fw_raw.strip() if isinstance(fw_raw, str) and fw_raw.strip() else None
    avail = (
        avail_raw.strip()
        if isinstance(avail_raw, str) and avail_raw.strip()
        else None
    )
    return fw, avail


def _firmware_version_state(data: dict[str, Any]) -> str | None:
    fw, avail = _firmware_versions(data)
    if avail and avail != fw:
        return f"{fw} → {avail}" if fw else avail
    return fw


def _firmware_version_icon(data: dict[str, Any]) -> str:
    fw, avail = _firmware_versions(data)
    if avail and avail != fw:
        return "mdi:package-up"
    return "mdi:package-check"


def _insight_section(
    coordinator: StuffyMeterCoordinator, key: str
) -> dict[str, Any] | None:
    ins = getattr(coordinator, "insights", None)
    if not isinstance(ins, dict):
        return None
    sec = ins.get(key)
    return sec if isinstance(sec, dict) else None


def _vent_state(coordinator: StuffyMeterCoordinator) -> str | None:
    vent = _insight_section(coordinator, "vent")
    return vent.get("state") if vent else None


def _vent_icon(coordinator: StuffyMeterCoordinator) -> str:
    state = _vent_state(coordinator)
    return _VENT_ICONS.get(state or "", "mdi:fan")


def _vent_attrs(coordinator: StuffyMeterCoordinator) -> dict[str, Any] | None:
    vent = _insight_section(coordinator, "vent")
    if not vent:
        return None
    attrs: dict[str, Any] = {
        "level": vent.get("level"),
        "action": vent.get("action"),
    }
    for k in ("rate_ppm_per_h", "eta_minutes", "target_ppm", "voc_index", "voc_level"):
        if vent.get(k) is not None:
            attrs[k] = vent[k]
    return attrs


def _co2_trend_rate(coordinator: StuffyMeterCoordinator) -> int | None:
    vent = _insight_section(coordinator, "vent")
    return vent.get("rate_ppm_per_h") if vent else None


def _comfort_state(coordinator: StuffyMeterCoordinator) -> str | None:
    comfort = _insight_section(coordinator, "comfort")
    return comfort.get("state") if comfort else None


def _comfort_icon(coordinator: StuffyMeterCoordinator) -> str:
    state = _comfort_state(coordinator)
    return _COMFORT_ICONS.get(state or "", "mdi:home-thermometer-outline")


def _comfort_attrs(coordinator: StuffyMeterCoordinator) -> dict[str, Any] | None:
    comfort = _insight_section(coordinator, "comfort")
    if not comfort:
        return None
    return {
        k: comfort[k]
        for k in ("level", "advice", "temperature_c", "humidity_rh", "dew_point_c")
        if comfort.get(k) is not None
    }


def _week_co2_avg(coordinator: StuffyMeterCoordinator) -> int | None:
    week = _insight_section(coordinator, "week")
    return week.get("co2_avg_ppm") if week else None


def _week_attrs(coordinator: StuffyMeterCoordinator) -> dict[str, Any] | None:
    week = _insight_section(coordinator, "week")
    if not week:
        return None
    attrs: dict[str, Any] = {
        "level": week.get("level"),
        "co2_min_ppm": week.get("co2_min_ppm"),
        "co2_max_ppm": week.get("co2_max_ppm"),
        "co2_hours_over_threshold": week.get("co2_hours_over_threshold"),
        "co2_threshold_ppm": week.get("co2_threshold_ppm"),
    }
    if week.get("has_voc"):
        attrs["voc_max"] = week.get("voc_max")
        attrs["voc_hours_over_threshold"] = week.get("voc_hours_over_threshold")
        attrs["voc_threshold"] = week.get("voc_threshold")
    if week.get("daily_breakdown") is not None:
        attrs["daily_breakdown"] = week["daily_breakdown"]
    return attrs


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
        key="ventilation_trend",
        translation_key="ventilation_trend",
        device_class=SensorDeviceClass.ENUM,
        options=VENTILATION_STATES,
        coord_value_fn=_vent_state,
        coord_icon_fn=_vent_icon,
        extra_attrs_fn=_vent_attrs,
    ),
    StuffyMeterSensorDescription(
        key="co2_trend_rate",
        translation_key="co2_trend_rate",
        icon="mdi:chart-line",
        native_unit_of_measurement="ppm/h",
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        coord_value_fn=_co2_trend_rate,
    ),
    StuffyMeterSensorDescription(
        key="comfort_mold",
        translation_key="comfort_mold",
        device_class=SensorDeviceClass.ENUM,
        options=COMFORT_STATES,
        coord_value_fn=_comfort_state,
        coord_icon_fn=_comfort_icon,
        extra_attrs_fn=_comfort_attrs,
    ),
    StuffyMeterSensorDescription(
        key="co2_7d_avg",
        translation_key="co2_7d_avg",
        device_class=SensorDeviceClass.CO2,
        native_unit_of_measurement=PPM_UNIT,
        state_class=SensorStateClass.MEASUREMENT,
        suggested_display_precision=0,
        icon="mdi:calendar-week",
        coord_value_fn=_week_co2_avg,
        extra_attrs_fn=_week_attrs,
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
    StuffyMeterSensorDescription(
        key="firmware_version",
        translation_key="firmware_version",
        entity_category=EntityCategory.DIAGNOSTIC,
        value_fn=_firmware_version_state,
        icon_fn=_firmware_version_icon,
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
        if self.entity_description.coord_value_fn is not None:
            return self.entity_description.coord_value_fn(self.coordinator)
        if not self.coordinator.data or self.entity_description.value_fn is None:
            return None
        return self.entity_description.value_fn(self.coordinator.data)

    @property
    def icon(self) -> str | None:
        """Return dynamic icon when configured, otherwise the description icon."""
        if self.entity_description.coord_icon_fn is not None:
            return self.entity_description.coord_icon_fn(self.coordinator)
        if self.entity_description.icon_fn and self.coordinator.data:
            return self.entity_description.icon_fn(self.coordinator.data)
        return self.entity_description.icon

    @property
    def extra_state_attributes(self) -> dict[str, Any] | None:
        """Return extra insight metadata attributes when configured."""
        if self.entity_description.extra_attrs_fn is not None:
            return self.entity_description.extra_attrs_fn(self.coordinator)
        return None
