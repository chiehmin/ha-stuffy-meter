"""Button platform for Stuffy Meter."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import StuffyMeterAuthError, StuffyMeterClient, StuffyMeterError
from .const import DOMAIN
from .coordinator import StuffyMeterCoordinator
from .entity import StuffyMeterEntity


@dataclass(frozen=True, kw_only=True)
class StuffyMeterButtonDescription(ButtonEntityDescription):
    """Describe a Stuffy Meter button entity."""

    press_fn: Callable[[StuffyMeterClient], Awaitable[object]]


BUTTONS: tuple[StuffyMeterButtonDescription, ...] = (
    StuffyMeterButtonDescription(
        key="identify",
        device_class=ButtonDeviceClass.IDENTIFY,
        entity_category=EntityCategory.DIAGNOSTIC,
        press_fn=lambda client: client.async_identify(),
    ),
    StuffyMeterButtonDescription(
        key="reboot",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda client: client.async_reboot(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Stuffy Meter button entities."""
    coordinator: StuffyMeterCoordinator = hass.data[DOMAIN][entry.entry_id]
    async_add_entities(
        StuffyMeterButton(coordinator, desc) for desc in BUTTONS
    )


class StuffyMeterButton(StuffyMeterEntity, ButtonEntity):
    """Representation of a Stuffy Meter button."""

    entity_description: StuffyMeterButtonDescription

    def __init__(
        self,
        coordinator: StuffyMeterCoordinator,
        description: StuffyMeterButtonDescription,
    ) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    async def async_press(self) -> None:
        """Invoke the button action on the monitor."""
        try:
            await self.entity_description.press_fn(self.coordinator.client)
        except StuffyMeterAuthError as err:
            raise HomeAssistantError(
                "Admin password is required for this action; set it in Stuffy Meter integration options."
            ) from err
        except StuffyMeterError as err:
            raise HomeAssistantError(str(err)) from err
