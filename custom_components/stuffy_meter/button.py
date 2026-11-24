"""Button platform for Stuffy Meter."""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from homeassistant.components.button import (
    ButtonDeviceClass,
    ButtonEntity,
    ButtonEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .api import StuffyMeterAuthError, StuffyMeterError
from .const import DOMAIN
from .coordinator import StuffyMeterConfigEntry, StuffyMeterCoordinator
from .entity import StuffyMeterEntity


@dataclass(frozen=True, kw_only=True)
class StuffyMeterButtonDescription(ButtonEntityDescription):
    """Describe a Stuffy Meter button entity."""

    press_fn: Callable[[StuffyMeterCoordinator], Awaitable[object]]


BUTTONS: tuple[StuffyMeterButtonDescription, ...] = (
    StuffyMeterButtonDescription(
        key="identify",
        device_class=ButtonDeviceClass.IDENTIFY,
        entity_category=EntityCategory.DIAGNOSTIC,
        press_fn=lambda coord: coord.client.async_identify(),
    ),
    StuffyMeterButtonDescription(
        key="reboot",
        device_class=ButtonDeviceClass.RESTART,
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda coord: coord.client.async_reboot(),
    ),
    StuffyMeterButtonDescription(
        key="check_updates",
        translation_key="check_updates",
        icon="mdi:update",
        entity_category=EntityCategory.CONFIG,
        press_fn=lambda coord: coord.async_check_for_updates(),
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: StuffyMeterConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up Stuffy Meter button entities."""
    coordinator = entry.runtime_data
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
            await self.entity_description.press_fn(self.coordinator)
        except StuffyMeterAuthError as err:
            if (
                self.coordinator.hass is not None
                and hasattr(self.coordinator.entry, "async_start_reauth")
            ):
                self.coordinator.entry.async_start_reauth(self.coordinator.hass)
            raise HomeAssistantError(
                "Admin password is required for this action; "
                "update it in Stuffy Meter integration options or reauthenticate.",
                translation_domain=DOMAIN,
                translation_key="auth_required",
            ) from err
        except StuffyMeterError as err:
            raise HomeAssistantError(
                str(err),
                translation_domain=DOMAIN,
                translation_key="api_error",
                translation_placeholders={"detail": str(err)},
            ) from err
