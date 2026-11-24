"""Config flow for Stuffy Meter."""

from __future__ import annotations

from collections.abc import Mapping
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
from homeassistant.helpers import selector
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

PASSWORD_SELECTOR = selector.TextSelector(
    selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD)
)


async def _validate_monitor(
    hass: Any,
    host: str,
    password: str = "",
    *,
    require_auth: bool = False,
) -> tuple[dict[str, Any], dict[str, str]]:
    """Probe /api/status (and /api/config if password provided or auth verification requested)."""
    errors: dict[str, str] = {}
    session = async_get_clientsession(hass)
    client = StuffyMeterClient(session, host=host, password=password)
    try:
        status = await client.async_get_status()
        if password or require_auth:
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
                dev_id = str(status["device"]["id"]).strip().lower()
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
                vol.Optional(CONF_PASSWORD): PASSWORD_SELECTOR,
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
        if discovery_info.ip_address.version != 4:
            return self.async_abort(reason="cannot_connect")
        host = discovery_info.host
        if discovery_info.port and discovery_info.port != 80:
            host = f"{host}:{discovery_info.port}"

        status, errors = await _validate_monitor(self.hass, host)
        if errors:
            return self.async_abort(reason=errors["base"])

        dev_id = str(status["device"]["id"]).strip().lower()
        await self.async_set_unique_id(dev_id)

        updates: dict[str, Any] | None = {CONF_HOST: host}
        for existing in self._async_current_entries():
            if existing.unique_id == dev_id:
                cur_host = str(existing.data.get(CONF_HOST, "")).strip().lower()
                if cur_host.split(":", 1)[0].endswith(".local"):
                    updates = None
                break
        self._abort_if_unique_id_configured(updates=updates)

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
                vol.Optional(CONF_PASSWORD): PASSWORD_SELECTOR,
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
        existing_password = entry.options.get(
            CONF_PASSWORD, entry.data.get(CONF_PASSWORD, "")
        )
        errors: dict[str, str] = {}

        if user_input is not None:
            host = normalize_host(user_input[CONF_HOST])
            entered_password = user_input.get(CONF_PASSWORD, "")
            password = entered_password or existing_password
            status, errors = await _validate_monitor(self.hass, host, password)
            if not errors:
                dev_id = str(status["device"]["id"]).strip().lower()
                await self.async_set_unique_id(dev_id)
                self._abort_if_unique_id_mismatch()
                new_options = {
                    k: v for k, v in entry.options.items() if k != CONF_PASSWORD
                }
                return self.async_update_reload_and_abort(
                    entry,
                    title=extract_device_name(status),
                    data_updates={
                        CONF_HOST: host,
                        CONF_PASSWORD: password,
                    },
                    options=new_options,
                )

        schema = vol.Schema(
            {
                vol.Required(
                    CONF_HOST,
                    default=(user_input or {}).get(CONF_HOST, entry.data[CONF_HOST]),
                ): str,
                vol.Optional(CONF_PASSWORD): PASSWORD_SELECTOR,
            }
        )
        return self.async_show_form(
            step_id="reconfigure",
            data_schema=schema,
            errors=errors,
        )

    async def async_step_reauth(
        self,
        entry_data: Mapping[str, Any],
    ) -> ConfigFlowResult:
        """Handle reauthentication when the monitor's admin password changed."""
        del entry_data
        return await self.async_step_reauth_confirm()

    async def async_step_reauth_confirm(
        self,
        user_input: dict[str, Any] | None = None,
    ) -> ConfigFlowResult:
        """Confirm updated admin credentials for an existing Stuffy Meter."""
        reauth_entry = self._get_reauth_entry()
        host = reauth_entry.data[CONF_HOST]
        errors: dict[str, str] = {}

        if user_input is not None:
            password = user_input.get(CONF_PASSWORD, "")
            status, errors = await _validate_monitor(
                self.hass,
                host,
                password,
                require_auth=True,
            )
            if not errors:
                new_options = {
                    k: v
                    for k, v in reauth_entry.options.items()
                    if k != CONF_PASSWORD
                }
                return self.async_update_reload_and_abort(
                    reauth_entry,
                    title=extract_device_name(status),
                    data_updates={CONF_PASSWORD: password},
                    options=new_options,
                )

        schema = vol.Schema(
            {
                vol.Optional(CONF_PASSWORD): PASSWORD_SELECTOR,
            }
        )
        return self.async_show_form(
            step_id="reauth_confirm",
            data_schema=schema,
            description_placeholders={
                "name": reauth_entry.title,
                "host": host,
            },
            errors=errors,
        )


class StuffyMeterOptionsFlow(OptionsFlow):
    """Handle Stuffy Meter options (polling interval and optional password update)."""

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

        if user_input is not None:
            entered_password = user_input.get(CONF_PASSWORD, "")
            if entered_password:
                _, errors = await _validate_monitor(
                    self.hass,
                    self.config_entry.data[CONF_HOST],
                    entered_password,
                    require_auth=True,
                )
            if not errors:
                legacy_opt_pw = self.config_entry.options.get(CONF_PASSWORD)
                if entered_password:
                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        data={
                            **self.config_entry.data,
                            CONF_PASSWORD: entered_password,
                        },
                    )
                elif (
                    legacy_opt_pw is not None
                    and legacy_opt_pw != self.config_entry.data.get(CONF_PASSWORD)
                ):
                    self.hass.config_entries.async_update_entry(
                        self.config_entry,
                        data={
                            **self.config_entry.data,
                            CONF_PASSWORD: legacy_opt_pw,
                        },
                    )
                return self.async_create_entry(
                    title="",
                    data={
                        CONF_SCAN_INTERVAL: int(user_input[CONF_SCAN_INTERVAL]),
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
                vol.Optional(CONF_PASSWORD): PASSWORD_SELECTOR,
            }
        )
        return self.async_show_form(
            step_id="init",
            data_schema=schema,
            errors=errors,
        )
