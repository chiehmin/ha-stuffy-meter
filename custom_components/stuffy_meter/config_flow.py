"""Config flow for Stuffy Meter."""

from __future__ import annotations

import logging
from typing import Any

import voluptuous as vol

from homeassistant.config_entries import (
    ConfigEntry,
    ConfigFlow,
    ConfigFlowResult,
    OptionsFlow,
)
from homeassistant.const import CONF_HOST, CONF_PASSWORD
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_get_clientsession

try:
    from homeassistant.helpers.service_info.zeroconf import ZeroconfServiceInfo
except ImportError:  # pragma: no cover - Home Assistant < 2025.1
    from homeassistant.components.zeroconf import ZeroconfServiceInfo  # type: ignore[no-redef]

from .api import (
    StuffyMeterAuthError,
    StuffyMeterClient,
    StuffyMeterConnectionError,
    StuffyMeterInvalidResponseError,
    normalize_host,
)
from .const import (
    CONF_SCAN_INTERVAL,
    DEFAULT_SCAN_INTERVAL,
    DOMAIN,
    MAX_SCAN_INTERVAL,
    MIN_SCAN_INTERVAL,
)
from .coordinator import extract_device_name

_LOGGER = logging.getLogger(__name__)


async def _validate_monitor(
    hass: Any,
    host: str,
    password: str = "",
) -> tuple[dict[str, Any], dict[str, str]]:
    """Probe /api/status (and /api/config if password provided)."""
    errors: dict[str, str] = {}
    session = async_get_clientsession(hass)
    client = StuffyMeterClient(session, host=host, password=password)
    try:
        status = await client.async_get_status()
        if password:
            await client.async_verify_auth()
    except StuffyMeterAuthError:
        errors["base"] = "invalid_auth"
        return {}, errors
    except StuffyMeterInvalidResponseError:
        errors["base"] = "invalid_device"
        return {}, errors
    except StuffyMeterConnectionError:
        errors["base"] = "cannot_connect"
        return {}, errors
    except Exception:  # noqa: BLE001
        _LOGGER.exception("Unexpected error probing Stuffy Meter at %s", host)
        errors["base"] = "unknown"
        return {}, errors

    return status, errors


class StuffyMeterConfigFlow(ConfigFlow, domain=DOMAIN):
    """Handle a config flow for Stuffy Meter."""

    VERSION = 1

    def __init__(self) -> None:
        self._discovered_host: str | None = None
        self._discovered_name: str | None = None
        self._discovered_id: str | None = None

    @staticmethod
    @callback
    def async_get_options_flow(config_entry: ConfigEntry) -> OptionsFlow:
        """Return the options flow handler."""
        return StuffyMeterOptionsFlow()

    async def async_step_user(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Handle manual setup by IP address or .local hostname."""
        errors: dict[str, str] = {}
        if user_input is not None:
            host = normalize_host(user_input[CONF_HOST])
            password = user_input.get(CONF_PASSWORD, "")
            status, errors = await _validate_monitor(self.hass, host, password)
            if not errors:
                dev_id = str(status["device"]["id"])
                await self.async_set_unique_id(dev_id)
                self._abort_if_unique_id_configured()
                return self.async_create_entry(
                    title=extract_device_name(status),
                    data={
                        CONF_HOST: host,
                        CONF_PASSWORD: password,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOST,
                    default=(user_input or {}).get(CONF_HOST, ""),
                ): str,
                vol.Optional(
                    CONF_PASSWORD,
                    default=(user_input or {}).get(CONF_PASSWORD, ""),
                ): str,
            }
        )
        return self.async_show_form(
            step_id="user",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_zeroconf(
        self,
        discovery_info: ZeroconfServiceInfo,
    ) -> ConfigFlowResult:
        """Handle automatic mDNS discovery (_stuffymeter._tcp / _smartaq._tcp)."""
        host = discovery_info.host
        props = discovery_info.properties or {}
        txt_id = props.get("id")

        if txt_id:
            await self.async_set_unique_id(str(txt_id))
            self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        status, errors = await _validate_monitor(self.hass, host)
        if errors:
            return self.async_abort(reason=errors["base"])

        dev_id = str(status["device"]["id"])
        await self.async_set_unique_id(dev_id)
        self._abort_if_unique_id_configured(updates={CONF_HOST: host})

        self._discovered_host = host
        self._discovered_id = dev_id
        self._discovered_name = extract_device_name(status)
        self.context["title_placeholders"] = {
            "name": self._discovered_name,
            "host": host,
        }
        return await self.async_step_zeroconf_confirm()

    async def async_step_zeroconf_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Confirm adding a discovered Stuffy Meter."""
        assert self._discovered_host is not None
        assert self._discovered_name is not None

        errors: dict[str, str] = {}
        if user_input is not None:
            password = user_input.get(CONF_PASSWORD, "")
            status, errors = await _validate_monitor(
                self.hass,
                self._discovered_host,
                password,
            )
            if not errors:
                return self.async_create_entry(
                    title=extract_device_name(status),
                    data={
                        CONF_HOST: self._discovered_host,
                        CONF_PASSWORD: password,
                    },
                )

        schema = vol.Schema(
            {
                vol.Optional(
                    CONF_PASSWORD,
                    default=(user_input or {}).get(CONF_PASSWORD, ""),
                ): str,
            }
        )
        return self.async_show_form(
            step_id="zeroconf_confirm",
            data_schema=schema,
            description_placeholders={
                "name": self._discovered_name,
                "host": self._discovered_host,
            },
            errors=errors,
        )

    async def async_step_reconfigure(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Allow changing host or admin password on an existing entry."""
        entry = self._get_reconfigure_entry()
        errors: dict[str, str] = {}

        if user_input is not None:
            host = normalize_host(user_input[CONF_HOST])
            password = user_input.get(CONF_PASSWORD, "")
            status, errors = await _validate_monitor(self.hass, host, password)
            if not errors:
                dev_id = str(status["device"]["id"])
                await self.async_set_unique_id(dev_id)
                self._abort_if_unique_id_mismatch()
                return self.async_update_reload_and_abort(
                    entry,
                    title=extract_device_name(status),
                    data_updates={
                        CONF_HOST: host,
                        CONF_PASSWORD: password,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOST,
                    default=(user_input or {}).get(CONF_HOST, entry.data[CONF_HOST]),
                ): str,
                vol.Optional(
                    CONF_PASSWORD,
                    default=(user_input or {}).get(
                        CONF_PASSWORD,
                        entry.options.get(
                            CONF_PASSWORD, entry.data.get(CONF_PASSWORD, "")
                        ),
                    ),
                ): str,
            }
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
        )


class StuffyMeterOptionsFlow(OptionsFlow):
    """Handle Stuffy Meter options (polling interval and admin password)."""

    async def async_step_init(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Manage integration options."""
        errors: dict[str, str] = {}
        current_interval = self.config_entry.options.get(
            CONF_SCAN_INTERVAL,
            self.config_entry.data.get(CONF_SCAN_INTERVAL, DEFAULT_SCAN_INTERVAL),
        )
        current_password = self.config_entry.options.get(
            CONF_PASSWORD,
            self.config_entry.data.get(CONF_PASSWORD, ""),
        )

        if user_input is not None:
            password = user_input.get(CONF_PASSWORD, "")
            if password:
                _, errors = await _validate_monitor(
                    self.hass,
                    self.config_entry.data[CONF_HOST],
                    password,
                )
            if not errors:
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
                        CONF_PASSWORD: password,
                    },
                )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_SCAN_INTERVAL,
                    default=(user_input or {}).get(
                        CONF_SCAN_INTERVAL, current_interval
                    ),
                ): vol.All(
                    vol.Coerce(int),
                    vol.Range(min=MIN_SCAN_INTERVAL, max=MAX_SCAN_INTERVAL),
                ),
                vol.Optional(
                    CONF_PASSWORD,
                    default=(user_input or {}).get(CONF_PASSWORD, current_password),
                ): str,
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )
