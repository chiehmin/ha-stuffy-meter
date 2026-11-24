"""The Stuffy Meter integration."""

from __future__ import annotations

from homeassistant.const import CONF_HOST, CONF_PASSWORD, Platform
from homeassistant.core import HomeAssistant
from homeassistant.helpers.aiohttp_client import async_get_clientsession

from .api import StuffyMeterClient
from .coordinator import StuffyMeterConfigEntry, StuffyMeterCoordinator

PLATFORMS: list[Platform] = [
    Platform.SENSOR,
    Platform.BINARY_SENSOR,
    Platform.BUTTON,
    Platform.UPDATE,
]


async def async_setup_entry(
    hass: HomeAssistant, entry: StuffyMeterConfigEntry
) -> bool:
    """Set up Stuffy Meter from a config entry."""
    host = entry.data[CONF_HOST]
    password = entry.options.get(CONF_PASSWORD, entry.data.get(CONF_PASSWORD, ""))

    session = async_get_clientsession(hass)
    client = StuffyMeterClient(session, host=host, password=password)
    coordinator = StuffyMeterCoordinator(hass, entry, client)
    await coordinator.async_config_entry_first_refresh()

    entry.runtime_data = coordinator

    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    entry.async_on_unload(entry.add_update_listener(_async_update_listener))
    return True


async def async_unload_entry(
    hass: HomeAssistant, entry: StuffyMeterConfigEntry
) -> bool:
    """Unload a Stuffy Meter config entry."""
    return await hass.config_entries.async_unload_platforms(entry, PLATFORMS)


async def _async_update_listener(
    hass: HomeAssistant, entry: StuffyMeterConfigEntry
) -> None:
    """Reload the integration when options change."""
    await hass.config_entries.async_reload(entry.entry_id)
