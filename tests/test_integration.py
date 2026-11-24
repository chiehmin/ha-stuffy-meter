"""Validation and API client tests for Stuffy Meter HACS integration."""

from __future__ import annotations

import ast
import asyncio
import base64
import importlib.util
import json
from pathlib import Path
import sys
import unittest

ROOT = Path(__file__).resolve().parent.parent
COMP = ROOT / "custom_components" / "stuffy_meter"


def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


def _flatten_keys(obj: dict, prefix: str = "") -> set[str]:
    out: set[str] = set()
    for k, v in obj.items():
        full = f"{prefix}.{k}" if prefix else k
        if isinstance(v, dict):
            out |= _flatten_keys(v, full)
        else:
            out.add(full)
    return out


class TestPackagingAndTranslations(unittest.TestCase):
    """Verify HACS and hassfest structural requirements."""

    def test_hacs_json(self) -> None:
        data = json.loads((ROOT / "hacs.json").read_text(encoding="utf-8"))
        self.assertEqual(data["name"], "Stuffy Meter")
        self.assertIn("homeassistant", data)

    def test_manifest_json_keys_and_order(self) -> None:
        raw = (COMP / "manifest.json").read_text(encoding="utf-8")
        data = json.loads(raw)
        keys = list(data.keys())
        self.assertEqual(keys[:2], ["domain", "name"])
        self.assertEqual(keys[2:], sorted(keys[2:]))
        self.assertEqual(data["domain"], "stuffy_meter")
        self.assertTrue(data["config_flow"])
        self.assertEqual(data["iot_class"], "local_polling")
        self.assertIn("_stuffymeter._tcp.local.", data["zeroconf"])

    def test_translation_parity(self) -> None:
        strings = json.loads((COMP / "strings.json").read_text(encoding="utf-8"))
        en = json.loads((COMP / "translations" / "en.json").read_text(encoding="utf-8"))
        zh = json.loads(
            (COMP / "translations" / "zh-Hant.json").read_text(encoding="utf-8")
        )
        self.assertEqual(strings, en)
        self.assertEqual(_flatten_keys(strings), _flatten_keys(zh))

    def test_all_python_files_parse(self) -> None:
        for py in sorted(COMP.glob("*.py")):
            ast.parse(py.read_text(encoding="utf-8"), filename=str(py))

    def test_modern_ha_api_constants(self) -> None:
        manifest = json.loads((COMP / "manifest.json").read_text(encoding="utf-8"))
        self.assertEqual(manifest["version"], "0.3.1")

        sensor_src = (COMP / "sensor.py").read_text(encoding="utf-8")
        self.assertIn("SensorDeviceClass.CO2", sensor_src)
        self.assertNotIn("SensorDeviceClass.CARBON_DIOXIDE", sensor_src)
        self.assertIn("UnitOfRatio.PARTS_PER_MILLION", sensor_src)
        self.assertIn("SensorDeviceClass.ABSOLUTE_HUMIDITY", sensor_src)
        self.assertIn("SensorDeviceClass.TIMESTAMP", sensor_src)
        self.assertIn("_unrecorded_attributes", sensor_src)
        self.assertIn("entry.runtime_data", sensor_src)
        self.assertNotIn("hass.data[DOMAIN]", sensor_src)

        coord_src = (COMP / "coordinator.py").read_text(encoding="utf-8")
        self.assertIn("async_get_device_by_identifier", coord_src)
        self.assertIn("StuffyMeterConfigEntry", coord_src)
        self.assertIn("dt_util.get_default_time_zone", coord_src)

        init_src = (COMP / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("Platform.UPDATE", init_src)
        self.assertIn("entry.runtime_data = coordinator", init_src)
        self.assertNotIn("hass.data", init_src)

        update_src = (COMP / "update.py").read_text(encoding="utf-8")
        self.assertIn("UpdateDeviceClass.FIRMWARE", update_src)
        self.assertIn("UpdateEntityFeature.INSTALL", update_src)
        self.assertIn("UpdateEntityFeature.PROGRESS", update_src)
        self.assertIn("def update_percentage", update_src)
        self.assertIn("entry.runtime_data", update_src)
        self.assertNotIn("hass.data[DOMAIN]", update_src)

        for plat in ("binary_sensor.py", "button.py"):
            src = (COMP / plat).read_text(encoding="utf-8")
            self.assertIn("entry.runtime_data", src)
            self.assertNotIn("hass.data[DOMAIN]", src)


def _install_ha_stubs() -> dict[str, Any]:
    """Install minimal homeassistant module stubs so integration modules can be unit-tested."""
    from dataclasses import dataclass
    from datetime import datetime, timezone
    from enum import IntFlag, StrEnum
    import types
    from unittest.mock import MagicMock

    saved: dict[str, Any] = {}

    def _register(name: str, mod: types.ModuleType) -> types.ModuleType:
        if name not in saved:
            saved[name] = sys.modules.get(name)
        sys.modules[name] = mod
        return mod

    if "aiohttp" not in sys.modules:
        try:
            import aiohttp  # noqa: F401
        except ImportError:
            aio_mod = _register("aiohttp", types.ModuleType("aiohttp"))

            class _ClientError(Exception):
                pass

            class _ContentTypeError(_ClientError):
                pass

            class _ClientTimeout:
                def __init__(self, total=None):
                    self.total = total

            aio_mod.ClientError = _ClientError
            aio_mod.ContentTypeError = _ContentTypeError
            aio_mod.ClientTimeout = _ClientTimeout
            aio_mod.ClientSession = object

    if "voluptuous" not in sys.modules:
        try:
            import voluptuous  # noqa: F401
        except ImportError:
            vol_mod = _register("voluptuous", types.ModuleType("voluptuous"))

            _UNDEF = object()

            class _Marker:
                def __init__(self, schema, default=_UNDEF):
                    self.schema = schema
                    self.default = default

                def __hash__(self):
                    return hash((type(self).__name__, self.schema))

                def __eq__(self, other):
                    return type(self) is type(other) and self.schema == other.schema

            class _Required(_Marker):
                pass

            class _Optional(_Marker):
                pass

            class _Schema:
                def __init__(self, schema):
                    self.schema = schema

                def __call__(self, data):
                    return data

            vol_mod.UNDEFINED = _UNDEF
            vol_mod.Required = _Required
            vol_mod.Optional = _Optional
            vol_mod.Schema = _Schema
            vol_mod.All = lambda *validators: validators
            vol_mod.Coerce = lambda typ: typ
            vol_mod.Range = lambda **kwargs: kwargs

    ha = _register("homeassistant", types.ModuleType("homeassistant"))
    ha.__path__ = []  # type: ignore[attr-defined]

    # homeassistant.util / homeassistant.util.dt
    util_mod = _register("homeassistant.util", types.ModuleType("homeassistant.util"))
    util_mod.__path__ = []  # type: ignore[attr-defined]
    dt_mod = _register("homeassistant.util.dt", types.ModuleType("homeassistant.util.dt"))
    dt_mod.DEFAULT_TIME_ZONE = timezone.utc
    dt_mod.get_default_time_zone = lambda: dt_mod.DEFAULT_TIME_ZONE
    dt_mod.utcnow = lambda: datetime.now(timezone.utc)
    util_mod.dt = dt_mod

    # homeassistant.const
    const_mod = _register("homeassistant.const", types.ModuleType("homeassistant.const"))
    const_mod.CONF_HOST = "host"
    const_mod.CONF_PASSWORD = "password"

    class EntityCategory(StrEnum):
        CONFIG = "config"
        DIAGNOSTIC = "diagnostic"

    class Platform(StrEnum):
        SENSOR = "sensor"
        BINARY_SENSOR = "binary_sensor"
        BUTTON = "button"
        UPDATE = "update"

    const_mod.EntityCategory = EntityCategory
    const_mod.Platform = Platform
    const_mod.PERCENTAGE = "%"
    const_mod.SIGNAL_STRENGTH_DECIBELS_MILLIWATT = "dBm"

    class UnitOfRatio(StrEnum):
        PARTS_PER_MILLION = "ppm"

    class UnitOfTemperature(StrEnum):
        CELSIUS = "°C"

    class UnitOfTime(StrEnum):
        SECONDS = "s"

    class UnitOfInformation(StrEnum):
        BYTES = "B"

    class UnitOfElectricPotential(StrEnum):
        VOLT = "V"

    const_mod.UnitOfRatio = UnitOfRatio
    const_mod.UnitOfTemperature = UnitOfTemperature
    const_mod.UnitOfTime = UnitOfTime
    const_mod.UnitOfInformation = UnitOfInformation
    const_mod.UnitOfElectricPotential = UnitOfElectricPotential

    # homeassistant.core
    core_mod = _register("homeassistant.core", types.ModuleType("homeassistant.core"))
    core_mod.HomeAssistant = object
    core_mod.callback = lambda fn: fn

    # homeassistant.config_entries
    ce_mod = _register(
        "homeassistant.config_entries",
        types.ModuleType("homeassistant.config_entries"),
    )

    class ConfigEntry:
        def __class_getitem__(cls, _item):
            return cls

    class AbortFlow(Exception):
        def __init__(self, reason: str) -> None:
            super().__init__(reason)
            self.reason = reason

    class ConfigFlow:
        def __init_subclass__(cls, *, domain: str | None = None, **kwargs):
            super().__init_subclass__(**kwargs)
            cls.domain = domain

        def __init__(self) -> None:
            self.hass = None
            self.context: dict = {}
            self._unique_id: str | None = None

        async def async_set_unique_id(self, unique_id: str | None) -> None:
            self._unique_id = unique_id

        def _async_current_entries(self):
            return []

        def _abort_if_unique_id_configured(self, *, updates=None):
            for entry in self._async_current_entries():
                if entry.unique_id == self._unique_id:
                    if updates is not None:
                        entry.data = {**entry.data, **updates}
                    raise AbortFlow("already_configured")

        def _abort_if_unique_id_mismatch(self):
            entry = self._get_reconfigure_entry()
            if entry.unique_id and self._unique_id != entry.unique_id:
                raise AbortFlow("unique_id_mismatch")

        def async_abort(self, *, reason: str):
            return {"type": "abort", "reason": reason}

        def async_show_form(
            self,
            *,
            step_id: str,
            data_schema=None,
            errors=None,
            description_placeholders=None,
        ):
            return {
                "type": "form",
                "step_id": step_id,
                "data_schema": data_schema,
                "errors": errors or {},
                "description_placeholders": description_placeholders or {},
            }

        def async_create_entry(self, *, title: str, data: dict):
            return {"type": "create_entry", "title": title, "data": data}

        def async_update_reload_and_abort(
            self,
            entry,
            *,
            title=None,
            data_updates=None,
            options=None,
        ):
            if data_updates:
                entry.data = {**entry.data, **data_updates}
            if options is not None:
                entry.options = dict(options)
            if title is not None:
                entry.title = title
            return {
                "type": "abort",
                "reason": "updated",
                "entry": entry,
            }

    class OptionsFlow:
        def __init__(self) -> None:
            self.hass = None
            self.config_entry = None

        def async_show_form(self, *, step_id: str, data_schema=None, errors=None):
            return {
                "type": "form",
                "step_id": step_id,
                "data_schema": data_schema,
                "errors": errors or {},
            }

        def async_create_entry(self, *, title: str, data: dict):
            return {"type": "create_entry", "title": title, "data": data}

    ce_mod.ConfigEntry = ConfigEntry
    ce_mod.ConfigFlow = ConfigFlow
    ce_mod.ConfigFlowResult = dict
    ce_mod.OptionsFlow = OptionsFlow
    ce_mod.AbortFlow = AbortFlow

    # homeassistant.exceptions
    exc_mod = _register(
        "homeassistant.exceptions",
        types.ModuleType("homeassistant.exceptions"),
    )

    class HomeAssistantError(Exception):
        def __init__(
            self,
            *args: object,
            translation_domain: str | None = None,
            translation_key: str | None = None,
            translation_placeholders: dict[str, str] | None = None,
        ) -> None:
            super().__init__(*args)
            self.translation_domain = translation_domain
            self.translation_key = translation_key
            self.translation_placeholders = translation_placeholders

    exc_mod.HomeAssistantError = HomeAssistantError

    # homeassistant.helpers.*
    helpers = _register("homeassistant.helpers", types.ModuleType("homeassistant.helpers"))
    helpers.__path__ = []  # type: ignore[attr-defined]

    aio_helper_mod = _register(
        "homeassistant.helpers.aiohttp_client",
        types.ModuleType("homeassistant.helpers.aiohttp_client"),
    )
    aio_helper_mod.async_get_clientsession = MagicMock()

    sel_mod = _register(
        "homeassistant.helpers.selector",
        types.ModuleType("homeassistant.helpers.selector"),
    )

    class TextSelectorType(StrEnum):
        PASSWORD = "password"

    class TextSelectorConfig:
        def __init__(self, *, type=None):
            self.type = type

    class TextSelector:
        def __init__(self, config=None):
            self.config = config

    sel_mod.TextSelector = TextSelector
    sel_mod.TextSelectorConfig = TextSelectorConfig
    sel_mod.TextSelectorType = TextSelectorType
    helpers.selector = sel_mod

    si_mod = _register(
        "homeassistant.helpers.service_info",
        types.ModuleType("homeassistant.helpers.service_info"),
    )
    si_mod.__path__ = []  # type: ignore[attr-defined]
    zc_si_mod = _register(
        "homeassistant.helpers.service_info.zeroconf",
        types.ModuleType("homeassistant.helpers.service_info.zeroconf"),
    )
    zc_si_mod.ZeroconfServiceInfo = object

    dr_mod = _register(
        "homeassistant.helpers.device_registry",
        types.ModuleType("homeassistant.helpers.device_registry"),
    )
    dr_mod.CONNECTION_NETWORK_MAC = "mac"

    class DeviceInfo(dict):
        def __init__(self, **kwargs):
            super().__init__(**kwargs)

    dr_mod.DeviceInfo = DeviceInfo
    dr_mod.async_get = MagicMock()
    helpers.device_registry = dr_mod

    ep_mod = _register(
        "homeassistant.helpers.entity_platform",
        types.ModuleType("homeassistant.helpers.entity_platform"),
    )
    ep_mod.AddEntitiesCallback = object

    uc_mod = _register(
        "homeassistant.helpers.update_coordinator",
        types.ModuleType("homeassistant.helpers.update_coordinator"),
    )

    class UpdateFailed(Exception):
        pass

    class DataUpdateCoordinator:
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, hass, logger, *, config_entry=None, name="", update_interval=None):
            self.hass = hass
            self.logger = logger
            self.config_entry = config_entry
            self.name = name
            self.update_interval = update_interval
            self.data = None
            self.last_update_success = True
            self._listeners: list = []

        async def async_config_entry_first_refresh(self):
            await self.async_refresh()

        async def async_refresh(self):
            try:
                self.data = await self._async_update_data()
            except UpdateFailed:
                self.last_update_success = False
            else:
                self.last_update_success = True
            for cb in list(self._listeners):
                cb()

        async def async_request_refresh(self):
            await self.async_refresh()

        def async_set_updated_data(self, data):
            self.data = data
            self.last_update_success = True
            for cb in list(self._listeners):
                cb()

        def async_set_update_error(self, err):
            self.last_exception = err
            self.last_update_success = False
            for cb in list(self._listeners):
                cb()

    class CoordinatorEntity:
        def __class_getitem__(cls, _item):
            return cls

        def __init__(self, coordinator):
            self.coordinator = coordinator
            self.state_writes: list[dict] = []

        @property
        def available(self) -> bool:
            return bool(self.coordinator.last_update_success)

        def async_write_ha_state(self) -> None:
            self.state_writes.append(
                {
                    "available": self.available,
                    "installed": getattr(self, "installed_version", None),
                    "latest": getattr(self, "latest_version", None),
                    "in_progress": getattr(self, "in_progress", None),
                    "percentage": getattr(self, "update_percentage", None),
                }
            )

    uc_mod.UpdateFailed = UpdateFailed
    uc_mod.DataUpdateCoordinator = DataUpdateCoordinator
    uc_mod.CoordinatorEntity = CoordinatorEntity

    # homeassistant.components.*
    comps = _register(
        "homeassistant.components",
        types.ModuleType("homeassistant.components"),
    )
    comps.__path__ = []  # type: ignore[attr-defined]

    upd_mod = _register(
        "homeassistant.components.update",
        types.ModuleType("homeassistant.components.update"),
    )

    class UpdateDeviceClass(StrEnum):
        FIRMWARE = "firmware"

    class UpdateEntityFeature(IntFlag):
        INSTALL = 1
        SPECIFIC_VERSION = 2
        PROGRESS = 4
        BACKUP = 8
        RELEASE_NOTES = 16

    class UpdateEntity:
        pass

    upd_mod.UpdateDeviceClass = UpdateDeviceClass
    upd_mod.UpdateEntity = UpdateEntity
    upd_mod.UpdateEntityFeature = UpdateEntityFeature

    btn_mod = _register(
        "homeassistant.components.button",
        types.ModuleType("homeassistant.components.button"),
    )

    class ButtonDeviceClass(StrEnum):
        IDENTIFY = "identify"
        RESTART = "restart"

    @dataclass(frozen=True, kw_only=True)
    class ButtonEntityDescription:
        key: str
        translation_key: str | None = None
        icon: str | None = None
        device_class: ButtonDeviceClass | None = None
        entity_category: EntityCategory | None = None

    class ButtonEntity:
        pass

    btn_mod.ButtonDeviceClass = ButtonDeviceClass
    btn_mod.ButtonEntity = ButtonEntity
    btn_mod.ButtonEntityDescription = ButtonEntityDescription

    bs_mod = _register(
        "homeassistant.components.binary_sensor",
        types.ModuleType("homeassistant.components.binary_sensor"),
    )

    class BinarySensorDeviceClass(StrEnum):
        PLUG = "plug"
        BATTERY_CHARGING = "battery_charging"

    @dataclass(frozen=True, kw_only=True)
    class BinarySensorEntityDescription:
        key: str
        translation_key: str | None = None
        icon: str | None = None
        device_class: BinarySensorDeviceClass | None = None
        entity_category: EntityCategory | None = None

    class BinarySensorEntity:
        pass

    bs_mod.BinarySensorDeviceClass = BinarySensorDeviceClass
    bs_mod.BinarySensorEntity = BinarySensorEntity
    bs_mod.BinarySensorEntityDescription = BinarySensorEntityDescription

    sens_mod = _register(
        "homeassistant.components.sensor",
        types.ModuleType("homeassistant.components.sensor"),
    )

    class SensorDeviceClass(StrEnum):
        CO2 = "carbon_dioxide"
        TEMPERATURE = "temperature"
        HUMIDITY = "humidity"
        ABSOLUTE_HUMIDITY = "absolute_humidity"
        ENUM = "enum"
        SIGNAL_STRENGTH = "signal_strength"
        DURATION = "duration"
        TIMESTAMP = "timestamp"
        DATA_SIZE = "data_size"
        BATTERY = "battery"
        VOLTAGE = "voltage"

    class SensorStateClass(StrEnum):
        MEASUREMENT = "measurement"
        TOTAL_INCREASING = "total_increasing"

    @dataclass(frozen=True, kw_only=True)
    class SensorEntityDescription:
        key: str
        translation_key: str | None = None
        icon: str | None = None
        device_class: SensorDeviceClass | None = None
        native_unit_of_measurement: str | None = None
        state_class: SensorStateClass | None = None
        suggested_display_precision: int | None = None
        options: list[str] | None = None
        entity_category: EntityCategory | None = None
        entity_registry_enabled_default: bool = True

    class SensorEntity:
        pass

    sens_mod.SensorDeviceClass = SensorDeviceClass
    sens_mod.SensorStateClass = SensorStateClass
    sens_mod.SensorEntity = SensorEntity
    sens_mod.SensorEntityDescription = SensorEntityDescription

    return saved


def _restore_modules(saved: dict[str, Any]) -> None:
    for name, prev in saved.items():
        if prev is None:
            sys.modules.pop(name, None)
        else:
            sys.modules[name] = prev


class TestApiClient(unittest.TestCase):
    """Test StuffyMeterClient against an in-process aiohttp server and stubs."""

    def test_normalize_host_and_stubbed_history_config(self) -> None:
        saved = _install_ha_stubs()
        try:
            pkg = type(sys)("custom_components.stuffy_meter")
            pkg.__path__ = [str(COMP)]
            sys.modules.setdefault("custom_components", type(sys)("custom_components"))
            sys.modules["custom_components.stuffy_meter"] = pkg
            _load_module("custom_components.stuffy_meter.const", COMP / "const.py")
            api = _load_module("custom_components.stuffy_meter.api", COMP / "api.py")

            # normalize_host strips scheme, path, query, fragment, whitespace
            self.assertEqual(api.normalize_host("http://192.168.1.42/"), "192.168.1.42")
            self.assertEqual(
                api.normalize_host("http://192.168.1.42/#dashboard"), "192.168.1.42"
            )
            self.assertEqual(
                api.normalize_host("192.168.1.42?foo=bar"), "192.168.1.42"
            )
            self.assertEqual(
                api.normalize_host("192.168.1.42#settings"), "192.168.1.42"
            )
            self.assertEqual(
                api.normalize_host("192.168.1.42:8080?x=1#y"), "192.168.1.42:8080"
            )
            self.assertEqual(
                api.normalize_host("  stuffymeter-a1b2c3.local/  "),
                "stuffymeter-a1b2c3.local",
            )
            self.assertEqual(
                api.normalize_host("stuffymeter-a1b2c3.local#dashboard"),
                "stuffymeter-a1b2c3.local",
            )

            # Stubbed session for async_get_history & async_get_config
            calls: list[tuple[str, str, dict | None, float | None]] = []

            class _FakeResp:
                def __init__(self, status: int, payload: dict) -> None:
                    self.status = status
                    self._payload = payload

                async def __aenter__(self):
                    return self

                async def __aexit__(self, *args):
                    return False

                async def json(self, content_type=None):
                    return self._payload

            class _FakeSession:
                def request(self, method, url, json=None, headers=None, timeout=None):
                    calls.append((method, url, headers, getattr(timeout, "total", None)))
                    if "/api/history" in url:
                        return _FakeResp(200, {"step_s": 60, "co2_ppm": [500, 510]})
                    if "/api/config" in url:
                        if not headers or not headers.get("Authorization"):
                            return _FakeResp(401, {"ok": False})
                        return _FakeResp(200, {"co2_t1": 800, "co2_t2": 1200})
                    return _FakeResp(404, {"error": "not found"})

            async def run() -> None:
                client = api.StuffyMeterClient(
                    _FakeSession(), "192.168.1.42?ignored=1", password="secret"
                )
                hist = await client.async_get_history(minutes=60, step=1)
                self.assertEqual(hist["co2_ppm"], [500, 510])
                self.assertEqual(
                    calls[-1][1], "http://192.168.1.42/api/history?minutes=60&step=1"
                )
                self.assertIsNone(calls[-1][2])
                self.assertEqual(calls[-1][3], 12)

                cfg = await client.async_get_config()
                self.assertEqual(cfg["co2_t2"], 1200)
                self.assertEqual(calls[-1][1], "http://192.168.1.42/api/config")
                self.assertIn("Authorization", calls[-1][2])

                pub = api.StuffyMeterClient(_FakeSession(), "192.168.1.42")
                with self.assertRaises(api.StuffyMeterAuthError):
                    await pub.async_get_config()

            asyncio.run(run())
        finally:
            for mod_name in list(sys.modules):
                if mod_name.startswith("custom_components.stuffy_meter"):
                    sys.modules.pop(mod_name, None)
            _restore_modules(saved)

    def test_client_endpoints(self) -> None:
        try:
            import aiohttp
            from aiohttp import web
        except ImportError:
            self.skipTest("aiohttp not installed in this Python environment")

        # Load const & api without importing homeassistant package
        pkg = type(sys)("custom_components.stuffy_meter")
        pkg.__path__ = [str(COMP)]
        sys.modules.setdefault("custom_components", type(sys)("custom_components"))
        sys.modules["custom_components.stuffy_meter"] = pkg
        _load_module("custom_components.stuffy_meter.const", COMP / "const.py")
        api = _load_module("custom_components.stuffy_meter.api", COMP / "api.py")

        self.assertEqual(api.normalize_host("http://192.168.1.42/"), "192.168.1.42")
        self.assertEqual(
            api.normalize_host("http://192.168.1.42/#dashboard"), "192.168.1.42"
        )
        self.assertEqual(
            api.normalize_host("  stuffymeter-a1b2c3.local/  "),
            "stuffymeter-a1b2c3.local",
        )

        expected_auth = "Basic " + base64.b64encode(b"admin:secret123").decode()
        identify_payloads: list[dict] = []
        ota_check_payloads: list[dict] = []
        reboot_calls = 0
        busy_check = False

        async def handle_status(request: web.Request) -> web.Response:
            return web.json_response(
                {
                    "device": {
                        "id": "a1b2c3",
                        "name": "客廳",
                        "board": "3.2",
                        "fw_version": "1.10.2",
                        "uptime_s": 3600,
                        "heap_free": 80000,
                        "auth": True,
                    },
                    "readings": {
                        "co2_ppm": 650,
                        "temperature_c": 24.5,
                        "humidity_rh": 50.0,
                        "dew_point_c": 13.4,
                        "abs_humidity_gm3": 11.2,
                        "voc_index": 102,
                        "level": "good",
                    },
                    "network": {"rssi": -48, "mac": "aa:bb:cc:dd:ee:ff"},
                    "ota": {
                        "state": "idle",
                        "progress": 0,
                        "available_version": "1.10.3",
                        "new_version": "",
                        "last_error": "",
                    },
                }
            )

        async def handle_config(request: web.Request) -> web.Response:
            if request.headers.get("Authorization") != expected_auth:
                return web.json_response({"ok": False}, status=401)
            return web.json_response({"device_name": "客廳"})

        async def handle_identify(request: web.Request) -> web.Response:
            self.assertEqual(request.content_type, "application/json")
            identify_payloads.append(await request.json())
            return web.json_response({"ok": True})

        async def handle_reboot(request: web.Request) -> web.Response:
            nonlocal reboot_calls
            self.assertEqual(request.content_type, "application/json")
            if request.headers.get("Authorization") != expected_auth:
                return web.json_response({"ok": False}, status=401)
            reboot_calls += 1
            return web.json_response({"ok": True})

        async def handle_ota_get(request: web.Request) -> web.Response:
            return web.json_response(
                {
                    "state": "idle",
                    "progress": 0,
                    "available_version": "1.10.3",
                    "auto": {
                        "enabled": True,
                        "last_result": "update_available",
                        "manifest_version": "1.10.3",
                        "notes_url": "https://github.com/chiehmin/stuffy-meter-releases/releases/tag/v1.10.3",
                    },
                }
            )

        async def handle_ota_check(request: web.Request) -> web.Response:
            self.assertEqual(request.content_type, "application/json")
            if request.headers.get("Authorization") != expected_auth:
                return web.json_response({"ok": False}, status=401)
            if busy_check:
                return web.json_response(
                    {"ok": False, "error": "an update or check is already running"},
                    status=409,
                )
            body = await request.json()
            ota_check_payloads.append(body)
            return web.json_response(
                {
                    "ok": True,
                    "current": "1.10.2",
                    "available": "1.10.3",
                    "update": True,
                    "installing": bool(body.get("install")),
                }
            )

        async def run() -> None:
            nonlocal busy_check
            app = web.Application()
            app.router.add_get("/api/status", handle_status)
            app.router.add_get("/api/config", handle_config)
            app.router.add_post("/api/identify", handle_identify)
            app.router.add_post("/api/reboot", handle_reboot)
            app.router.add_get("/api/ota", handle_ota_get)
            app.router.add_post("/api/ota/check", handle_ota_check)
            runner = web.AppRunner(app)
            await runner.setup()
            site = web.TCPSite(runner, "127.0.0.1", 0)
            await site.start()
            port = site._server.sockets[0].getsockname()[1]

            try:
                async with aiohttp.ClientSession() as session:
                    # Public access without password
                    pub = api.StuffyMeterClient(session, f"127.0.0.1:{port}")
                    status = await pub.async_get_status()
                    self.assertEqual(status["device"]["id"], "a1b2c3")
                    self.assertEqual(status["readings"]["co2_ppm"], 650)

                    ota_info = await pub.async_get_ota()
                    self.assertEqual(ota_info["available_version"], "1.10.3")
                    self.assertIn("releases/tag/v1.10.3", ota_info["auto"]["notes_url"])

                    await pub.async_identify(15)
                    self.assertEqual(identify_payloads, [{"seconds": 15}])

                    with self.assertRaises(api.StuffyMeterAuthError):
                        await pub.async_reboot()

                    with self.assertRaises(api.StuffyMeterAuthError):
                        await pub.async_check_ota(install=False)

                    # Authenticated access
                    authed = api.StuffyMeterClient(
                        session, f"127.0.0.1:{port}", password="secret123"
                    )
                    cfg = await authed.async_verify_auth()
                    self.assertEqual(cfg["device_name"], "客廳")
                    await authed.async_reboot()
                    self.assertEqual(reboot_calls, 1)

                    chk = await authed.async_check_ota(install=False)
                    self.assertTrue(chk["update"])
                    self.assertFalse(chk["installing"])

                    inst = await authed.async_check_ota(install=True)
                    self.assertTrue(inst["installing"])
                    self.assertEqual(
                        ota_check_payloads,
                        [{"install": False}, {"install": True}],
                    )

                    # JSON error extraction on HTTP 409
                    busy_check = True
                    with self.assertRaises(api.StuffyMeterApiError) as ctx:
                        await authed.async_check_ota(install=True)
                    self.assertEqual(ctx.exception.status, 409)
                    self.assertIn("already running", str(ctx.exception))
            finally:
                await runner.cleanup()

        asyncio.run(run())


class TestFirmwareUpdateEntity(unittest.TestCase):
    """Test StuffyMeterFirmwareUpdate and Check for updates button end-to-end."""

    def setUp(self) -> None:
        self._saved_mods = _install_ha_stubs()
        pkg = type(sys)("custom_components.stuffy_meter")
        pkg.__path__ = [str(COMP)]
        sys.modules.setdefault("custom_components", type(sys)("custom_components"))
        sys.modules["custom_components.stuffy_meter"] = pkg
        self.const = _load_module("custom_components.stuffy_meter.const", COMP / "const.py")
        self.api = _load_module("custom_components.stuffy_meter.api", COMP / "api.py")
        self.insights_mod = _load_module(
            "custom_components.stuffy_meter.insights", COMP / "insights.py"
        )
        self.coord_mod = _load_module(
            "custom_components.stuffy_meter.coordinator", COMP / "coordinator.py"
        )
        self.entity_mod = _load_module(
            "custom_components.stuffy_meter.entity", COMP / "entity.py"
        )
        self.update_mod = _load_module(
            "custom_components.stuffy_meter.update", COMP / "update.py"
        )
        self.button_mod = _load_module(
            "custom_components.stuffy_meter.button", COMP / "button.py"
        )
        # Speed up polling in tests
        self.update_mod.OTA_INSTALL_POLL_INTERVAL = 0.01
        self.update_mod.OTA_INSTALL_START_GRACE = 0.05
        self.update_mod.OTA_REBOOT_TIMEOUT = 0.2
        self.update_mod.OTA_INSTALL_TIMEOUT = 2.0

    def tearDown(self) -> None:
        for mod_name in list(sys.modules):
            if mod_name.startswith("custom_components.stuffy_meter"):
                sys.modules.pop(mod_name, None)
        _restore_modules(self._saved_mods)

    def _make_entry(self):
        from types import SimpleNamespace

        return SimpleNamespace(
            entry_id="entry-1",
            unique_id="a1b2c3",
            data={"host": "127.0.0.1"},
            options={},
        )

    def _make_status(
        self,
        *,
        fw_version: str = "1.10.2",
        boot_count: int = 4,
        uptime_s: int = 3600,
        ota_state: str = "idle",
        progress: int = 0,
        available_version: str = "",
        new_version: str = "",
        last_error: str = "",
    ) -> dict:
        return {
            "device": {
                "id": "a1b2c3",
                "name": "客廳",
                "board": "3.2",
                "fw_version": fw_version,
                "boot_count": boot_count,
                "uptime_s": uptime_s,
            },
            "readings": {"co2_ppm": 600},
            "network": {"mac": "aa:bb:cc:dd:ee:ff"},
            "ota": {
                "state": ota_state,
                "progress": progress,
                "available_version": available_version,
                "new_version": new_version,
                "last_error": last_error,
            },
        }

    def test_properties_and_install_happy_path(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock
        from homeassistant.helpers import device_registry as dr

        dev_entry = SimpleNamespace(id="dev-1", name="客廳", sw_version="1.10.2")
        dev_reg = MagicMock()
        dev_reg.async_get_device_by_identifier.return_value = dev_entry
        dr.async_get.return_value = dev_reg

        client = MagicMock()
        client.host = "192.168.1.42"
        client.has_password = False
        client.async_get_history = AsyncMock(return_value={})
        client.base_url = "http://192.168.1.42"
        client.async_get_ota = AsyncMock(
            return_value={
                "state": "idle",
                "available_version": "1.10.3",
                "auto": {
                    "last_result": "update_available",
                    "manifest_version": "1.10.3",
                    "notes_url": "https://github.com/chiehmin/stuffy-meter-releases/releases/tag/v1.10.3",
                },
            }
        )
        client.async_check_ota = AsyncMock(
            return_value={
                "ok": True,
                "current": "1.10.2",
                "available": "1.10.3",
                "update": True,
                "installing": True,
            }
        )

        # Poll sequence during install: downloading 25% -> 80% -> success -> reboot drop -> new fw
        poll_steps = iter(
            [
                self._make_status(
                    ota_state="downloading",
                    progress=25,
                    available_version="1.10.3",
                    new_version="1.10.3",
                ),
                self._make_status(
                    ota_state="downloading",
                    progress=80,
                    available_version="1.10.3",
                    new_version="1.10.3",
                ),
                self._make_status(
                    ota_state="success",
                    progress=100,
                    available_version="1.10.3",
                    new_version="1.10.3",
                ),
                self.api.StuffyMeterConnectionError("rebooting"),
                self.api.StuffyMeterConnectionError("still rebooting"),
                self._make_status(
                    fw_version="1.10.3",
                    boot_count=5,
                    uptime_s=12,
                    ota_state="idle",
                    progress=0,
                    available_version="",
                ),
            ]
        )

        async def fake_status():
            nxt = next(poll_steps)
            if isinstance(nxt, Exception):
                raise nxt
            return nxt

        client.async_get_status = AsyncMock(
            return_value=self._make_status(available_version="1.10.3")
        )

        coord = self.coord_mod.StuffyMeterCoordinator(object(), self._make_entry(), client)
        entity = self.update_mod.StuffyMeterFirmwareUpdate(coord)
        coord._listeners.append(entity.async_write_ha_state)

        async def run() -> None:
            await coord.async_refresh()
            self.assertEqual(entity.installed_version, "1.10.2")
            self.assertEqual(entity.latest_version, "1.10.3")
            self.assertEqual(
                entity.release_url,
                "https://github.com/chiehmin/stuffy-meter-releases/releases/tag/v1.10.3",
            )
            self.assertFalse(entity.in_progress)
            self.assertIsNone(entity.update_percentage)

            # Now run install
            client.async_get_status = AsyncMock(side_effect=fake_status)
            await entity.async_install(version=None, backup=False)

            self.assertEqual(entity.installed_version, "1.10.3")
            self.assertEqual(entity.latest_version, "1.10.3")
            self.assertFalse(entity.in_progress)
            self.assertIsNone(entity.update_percentage)
            self.assertTrue(entity.available)

            # Confirm progress snapshots were written (0 -> 25 -> 80 -> 100 -> None)
            pcts = [w["percentage"] for w in entity.state_writes]
            self.assertIn(0, pcts)
            self.assertIn(25, pcts)
            self.assertIn(80, pcts)
            self.assertIn(100, pcts)
            self.assertIsNone(pcts[-1])

            # Confirm device registry sw_version was updated
            dev_reg.async_update_device.assert_called_with("dev-1", sw_version="1.10.3")

        asyncio.run(run())

    def test_install_failure_and_rollback_and_auth(self) -> None:
        from unittest.mock import AsyncMock, MagicMock
        from homeassistant.exceptions import HomeAssistantError
        from homeassistant.helpers import device_registry as dr

        dr.async_get.return_value = MagicMock(
            async_get_device_by_identifier=MagicMock(return_value=None)
        )

        client = MagicMock()
        client.host = "192.168.1.42"
        client.has_password = False
        client.async_get_history = AsyncMock(return_value={})
        client.async_get_ota = AsyncMock(return_value={"auto": {}})

        coord = self.coord_mod.StuffyMeterCoordinator(object(), self._make_entry(), client)
        coord.data = self._make_status(available_version="1.10.3")
        entity = self.update_mod.StuffyMeterFirmwareUpdate(coord)

        async def run() -> None:
            # 1. Auth error
            client.async_check_ota = AsyncMock(
                side_effect=self.api.StuffyMeterAuthError("HTTP 401")
            )
            with self.assertRaises(HomeAssistantError) as ctx:
                await entity.async_install(version=None, backup=False)
            self.assertIn("Admin password", str(ctx.exception))
            self.assertFalse(entity.in_progress)

            # 2. Download failed state
            client.async_check_ota = AsyncMock(
                return_value={"ok": True, "update": True, "installing": True, "available": "1.10.3"}
            )
            client.async_get_status = AsyncMock(
                return_value=self._make_status(
                    ota_state="failed",
                    last_error="sha256 mismatch",
                    available_version="1.10.3",
                )
            )
            with self.assertRaises(HomeAssistantError) as ctx:
                await entity.async_install(version=None, backup=False)
            self.assertIn("sha256 mismatch", str(ctx.exception))
            self.assertFalse(entity.in_progress)

            # 3. Rollback after reboot (boot_count changed, fw_version stayed 1.10.2)
            coord.data = self._make_status(
                fw_version="1.10.2", boot_count=4, available_version="1.10.3"
            )
            client.async_get_status = AsyncMock(
                return_value=self._make_status(
                    fw_version="1.10.2",
                    boot_count=5,
                    uptime_s=15,
                    ota_state="idle",
                    last_error="rolled back from 1.10.3 (crashed: panic)",
                )
            )
            with self.assertRaises(HomeAssistantError) as ctx:
                await entity.async_install(version=None, backup=False)
            self.assertIn("rolled back", str(ctx.exception))
            self.assertFalse(entity.in_progress)

            # 4. Already up to date
            client.async_check_ota = AsyncMock(
                return_value={"ok": True, "update": False, "installing": False, "available": "1.10.2"}
            )
            client.async_get_status = AsyncMock(
                return_value=self._make_status(fw_version="1.10.2", available_version="")
            )
            await entity.async_install(version=None, backup=False)
            self.assertEqual(entity.latest_version, "1.10.2")
            self.assertFalse(entity.in_progress)

            # 5. External OTA progress reflected when HA didn't start it
            coord.data = self._make_status(
                ota_state="downloading", progress=63, new_version="1.10.4"
            )
            self.assertTrue(entity.in_progress)
            self.assertEqual(entity.update_percentage, 63)
            self.assertEqual(entity.latest_version, "1.10.4")

        asyncio.run(run())

    def test_check_for_updates_action_and_button(self) -> None:
        from unittest.mock import AsyncMock, MagicMock
        from homeassistant.exceptions import HomeAssistantError
        from homeassistant.helpers import device_registry as dr

        dr.async_get.return_value = MagicMock(
            async_get_device_by_identifier=MagicMock(return_value=None)
        )

        client = MagicMock()
        client.host = "192.168.1.42"
        client.has_password = False
        client.async_get_history = AsyncMock(return_value={})
        client.async_get_ota = AsyncMock(
            return_value={
                "auto": {
                    "last_result": "update_available",
                    "notes_url": "https://github.com/chiehmin/stuffy-meter-releases/releases/tag/v1.11.0",
                }
            }
        )
        client.async_get_status = AsyncMock(
            return_value=self._make_status(available_version="1.11.0")
        )
        client.async_check_ota = AsyncMock(
            return_value={"ok": True, "current": "1.10.2", "available": "1.11.0", "update": True, "installing": False}
        )

        coord = self.coord_mod.StuffyMeterCoordinator(object(), self._make_entry(), client)
        coord.data = self._make_status(available_version="")
        entity = self.update_mod.StuffyMeterFirmwareUpdate(coord)
        check_desc = next(b for b in self.button_mod.BUTTONS if b.key == "check_updates")
        button = self.button_mod.StuffyMeterButton(coord, check_desc)

        async def run() -> None:
            # UpdateEntity.async_update() triggers check and refreshes
            await entity.async_update()
            client.async_check_ota.assert_awaited_with(install=False)
            self.assertEqual(entity.latest_version, "1.11.0")

            # Auth error in async_update degrades gracefully (no crash in Settings -> Updates)
            client.async_check_ota = AsyncMock(
                side_effect=self.api.StuffyMeterAuthError("HTTP 401")
            )
            await entity.async_update()

            # Button press raises HomeAssistantError on auth failure
            with self.assertRaises(HomeAssistantError):
                await button.async_press()

            # Button press succeeds when authorized
            client.async_check_ota = AsyncMock(
                return_value={"ok": True, "update": True, "installing": False}
            )
            await button.async_press()
            client.async_check_ota.assert_awaited_with(install=False)

        asyncio.run(run())

    def test_coordinator_ota_caching_and_install_timeouts(self) -> None:
        from unittest.mock import AsyncMock, MagicMock
        from homeassistant.exceptions import HomeAssistantError
        from homeassistant.helpers import device_registry as dr

        dr.async_get.return_value = MagicMock(
            async_get_device_by_identifier=MagicMock(return_value=None)
        )

        client = MagicMock()
        client.host = "192.168.1.42"
        client.has_password = False
        client.async_get_history = AsyncMock(return_value={})
        client.async_get_status = AsyncMock(
            return_value=self._make_status(available_version="")
        )
        client.async_get_ota = AsyncMock(
            return_value={"auto": {"last_result": "up_to_date", "notes_url": ""}}
        )

        coord = self.coord_mod.StuffyMeterCoordinator(object(), self._make_entry(), client)
        entity = self.update_mod.StuffyMeterFirmwareUpdate(coord)

        async def run() -> None:
            # First refresh fetches /api/ota once; second steady refresh does NOT re-fetch
            await coord.async_refresh()
            await coord.async_refresh()
            self.assertEqual(client.async_get_ota.await_count, 1)

            # When available_version appears in /api/status, /api/ota IS re-fetched immediately
            client.async_get_status = AsyncMock(
                return_value=self._make_status(available_version="1.10.3")
            )
            await coord.async_refresh()
            self.assertEqual(client.async_get_ota.await_count, 2)

            # A failing /api/ota must not break the main sensor status refresh
            client.async_get_ota = AsyncMock(
                side_effect=self.api.StuffyMeterConnectionError("temporary error")
            )
            client.async_get_status = AsyncMock(
                return_value=self._make_status(available_version="1.10.4")
            )
            await coord.async_refresh()
            self.assertTrue(coord.last_update_success)
            self.assertEqual(entity.latest_version, "1.10.4")

            # Start grace timeout when monitor stays idle after installing=True
            client.async_check_ota = AsyncMock(
                return_value={"ok": True, "update": True, "installing": True, "available": "1.10.4"}
            )
            client.async_get_status = AsyncMock(
                return_value=self._make_status(ota_state="idle", available_version="1.10.4")
            )
            with self.assertRaises(HomeAssistantError) as ctx:
                await entity.async_install(version=None, backup=False)
            self.assertIn("did not start", str(ctx.exception))
            self.assertFalse(entity.in_progress)

            # Reboot timeout when monitor disconnects during reboot and never returns
            poll_seq = iter(
                [
                    self._make_status(ota_state="success", progress=100, available_version="1.10.4"),
                ]
            )

            async def drop_after_success():
                try:
                    return next(poll_seq)
                except StopIteration as err:
                    raise self.api.StuffyMeterConnectionError("offline") from err

            client.async_get_status = AsyncMock(side_effect=drop_after_success)
            with self.assertRaises(HomeAssistantError) as ctx:
                await entity.async_install(version=None, backup=False)
            self.assertIn("did not come back online", str(ctx.exception))
            self.assertFalse(entity.in_progress)

        asyncio.run(run())

    def test_update_icons_summary_and_firmware_sensor(self) -> None:
        from unittest.mock import AsyncMock, MagicMock

        sensor_mod = _load_module(
            "custom_components.stuffy_meter.sensor", COMP / "sensor.py"
        )

        client = MagicMock()
        client.host = "192.168.1.42"
        client.has_password = False
        client.async_get_history = AsyncMock(return_value={})
        coord = self.coord_mod.StuffyMeterCoordinator(object(), self._make_entry(), client)
        upd = self.update_mod.StuffyMeterFirmwareUpdate(coord)
        fw_desc = next(s for s in sensor_mod.BASE_SENSORS if s.key == "firmware_version")
        fw_sensor = sensor_mod.StuffyMeterSensor(coord, fw_desc)

        # 1. Up to date (native HA MDI icon, no brand entity_picture)
        coord.data = self._make_status(fw_version="1.10.2", available_version="")
        self.assertIsNone(upd.entity_picture)
        self.assertEqual(upd.icon, "mdi:package-check")
        self.assertIsNone(upd.release_summary)
        self.assertEqual(fw_sensor.native_value, "1.10.2")
        self.assertEqual(fw_sensor.icon, "mdi:package-check")

        # 2. Update available
        coord.data = self._make_status(fw_version="1.10.2", available_version="2.0.1")
        self.assertIsNone(upd.entity_picture)
        self.assertEqual(upd.icon, "mdi:package-up")
        self.assertEqual(
            upd.release_summary,
            "2.0.1 is available for update (installed: 1.10.2).",
        )
        self.assertEqual(fw_sensor.native_value, "1.10.2 → 2.0.1")
        self.assertEqual(fw_sensor.icon, "mdi:package-up")

        # 3. Installing
        coord.data = self._make_status(
            fw_version="1.10.2",
            ota_state="downloading",
            progress=40,
            available_version="2.0.1",
            new_version="2.0.1",
        )
        self.assertIsNone(upd.entity_picture)
        self.assertEqual(upd.icon, "mdi:progress-download")

    def test_concurrent_install_guard_and_idle_since_long_ota(self) -> None:
        from unittest.mock import AsyncMock, MagicMock
        from homeassistant.exceptions import HomeAssistantError
        from homeassistant.helpers import device_registry as dr

        dr.async_get.return_value = MagicMock(
            async_get_device_by_identifier=MagicMock(return_value=None)
        )

        client = MagicMock()
        client.host = "192.168.1.42"
        client.has_password = True
        client.async_get_history = AsyncMock(return_value={})
        client.async_get_ota = AsyncMock(return_value={"auto": {}})

        coord = self.coord_mod.StuffyMeterCoordinator(object(), self._make_entry(), client)
        coord.data = self._make_status(available_version="1.10.3")
        entity = self.update_mod.StuffyMeterFirmwareUpdate(coord)

        async def run() -> None:
            # 1. Concurrent async_install raises immediately WITHOUT wiping active state
            gate = asyncio.Event()
            started_evt = asyncio.Event()

            async def slow_check_ota(install=True):
                started_evt.set()
                await gate.wait()
                return {
                    "ok": True,
                    "update": True,
                    "installing": True,
                    "available": "1.10.3",
                }

            client.async_check_ota = AsyncMock(side_effect=slow_check_ota)
            client.async_get_status = AsyncMock(
                return_value=self._make_status(
                    fw_version="1.10.3", boot_count=5, uptime_s=5
                )
            )

            task1 = asyncio.create_task(entity.async_install(version=None, backup=False))
            await started_evt.wait()
            self.assertTrue(entity.in_progress)
            self.assertEqual(entity._install_percentage, 0)
            self.assertEqual(entity._install_target, "1.10.3")

            with self.assertRaises(HomeAssistantError) as ctx:
                await entity.async_install(version=None, backup=False)
            self.assertEqual(ctx.exception.translation_domain, "stuffy_meter")
            self.assertEqual(ctx.exception.translation_key, "ota_already_in_progress")

            # Active install state MUST still be intact after duplicate call rejected
            self.assertTrue(entity._install_in_progress)
            self.assertEqual(entity._install_percentage, 0)
            self.assertEqual(entity._install_target, "1.10.3")

            gate.set()
            await task1
            self.assertEqual(entity.installed_version, "1.10.3")
            self.assertFalse(entity.in_progress)

            # 2. Long OTA (> OTA_INSTALL_START_GRACE) with transient idle poll succeeds
            coord.data = self._make_status(
                fw_version="1.10.3", boot_count=5, available_version="1.10.4"
            )
            client.async_check_ota = AsyncMock(
                return_value={
                    "ok": True,
                    "update": True,
                    "installing": True,
                    "available": "1.10.4",
                }
            )
            # 8 downloading polls (~0.08s > 0.05s grace), then 1 transient idle poll,
            # then reboot disconnect, then new fw boots!
            long_steps = iter(
                [
                    self._make_status(
                        fw_version="1.10.3",
                        boot_count=5,
                        ota_state="downloading",
                        progress=min(95, 10 * (i + 1)),
                        available_version="1.10.4",
                    )
                    for i in range(8)
                ]
                + [
                    self._make_status(
                        fw_version="1.10.3",
                        boot_count=5,
                        ota_state="idle",
                        progress=0,
                        available_version="1.10.4",
                    ),
                    self.api.StuffyMeterConnectionError("rebooting"),
                    self._make_status(
                        fw_version="1.10.4",
                        boot_count=6,
                        uptime_s=4,
                        ota_state="idle",
                    ),
                ]
            )

            async def fake_long_status():
                nxt = next(long_steps)
                if isinstance(nxt, Exception):
                    raise nxt
                return nxt

            client.async_get_status = AsyncMock(side_effect=fake_long_status)
            await entity.async_install(version=None, backup=False)
            self.assertEqual(entity.installed_version, "1.10.4")
            self.assertFalse(entity.in_progress)

            # 3. Sustained idle >= OTA_INSTALL_START_GRACE after active state DOES fail
            coord.data = self._make_status(
                fw_version="1.10.4", boot_count=6, available_version="1.10.5"
            )
            client.async_check_ota = AsyncMock(
                return_value={
                    "ok": True,
                    "update": True,
                    "installing": True,
                    "available": "1.10.5",
                }
            )
            aborted_steps = iter(
                [
                    self._make_status(
                        fw_version="1.10.4",
                        boot_count=6,
                        ota_state="downloading",
                        progress=30,
                        available_version="1.10.5",
                    )
                ]
            )

            async def fake_aborted_status():
                try:
                    return next(aborted_steps)
                except StopIteration:
                    return self._make_status(
                        fw_version="1.10.4",
                        boot_count=6,
                        ota_state="idle",
                        available_version="1.10.5",
                    )

            client.async_get_status = AsyncMock(side_effect=fake_aborted_status)
            with self.assertRaises(HomeAssistantError) as ctx:
                await entity.async_install(version=None, backup=False)
            self.assertEqual(ctx.exception.translation_key, "ota_stopped_unexpectedly")
            self.assertIn("stopped unexpectedly", str(ctx.exception))
            self.assertFalse(entity.in_progress)

        asyncio.run(run())



class TestInsights(unittest.TestCase):
    """Test VOC-aware Insights calculations and Home Assistant Insight sensor entities."""

    def setUp(self) -> None:
        self._saved_mods = _install_ha_stubs()
        pkg = type(sys)("custom_components.stuffy_meter")
        pkg.__path__ = [str(COMP)]
        sys.modules.setdefault("custom_components", type(sys)("custom_components"))
        sys.modules["custom_components.stuffy_meter"] = pkg
        self.const = _load_module("custom_components.stuffy_meter.const", COMP / "const.py")
        self.api = _load_module("custom_components.stuffy_meter.api", COMP / "api.py")
        self.insights = _load_module(
            "custom_components.stuffy_meter.insights", COMP / "insights.py"
        )
        self.coord_mod = _load_module(
            "custom_components.stuffy_meter.coordinator", COMP / "coordinator.py"
        )
        self.entity_mod = _load_module(
            "custom_components.stuffy_meter.entity", COMP / "entity.py"
        )
        self.sensor_mod = _load_module(
            "custom_components.stuffy_meter.sensor", COMP / "sensor.py"
        )

    def tearDown(self) -> None:
        for mod_name in list(sys.modules):
            if mod_name.startswith("custom_components.stuffy_meter"):
                sys.modules.pop(mod_name, None)
        _restore_modules(self._saved_mods)

    def test_calc_vent_co2_and_voc_cases(self) -> None:
        # Rising CO2 (+180 ppm/h over 30 min -> 90 ppm rise)
        rising_hist = {"step_s": 60, "co2_ppm": [800 + i * 3 for i in range(31)]}
        res = self.insights.calc_vent(rising_hist, 900, (800, 1200, 1600), 105)
        self.assertIsNotNone(res)
        self.assertEqual(res["state"], "rising")
        self.assertEqual(res["rate_ppm_per_h"], 180)
        self.assertEqual(res["level"], "poor")
        self.assertEqual(res["eta_minutes"], 100)
        self.assertEqual(res["target_ppm"], 1200)
        self.assertEqual(res["action"], "none")

        # Steady CO2 + high VOC -> escalates level & action
        steady_hist = {"step_s": 60, "co2_ppm": [540] * 31}
        res_voc = self.insights.calc_vent(steady_hist, 540, (800, 1200, 1600), 320)
        self.assertEqual(res_voc["state"], "steady")
        self.assertEqual(res_voc["level"], "poor")
        self.assertEqual(res_voc["action"], "ventilate_now")
        self.assertEqual(res_voc["voc_index"], 320)

        # Ventilating CO2 (-240 ppm/h) + high VOC -> keep_airing_out
        vent_hist = {"step_s": 60, "co2_ppm": [1100 - i * 4 for i in range(31)]}
        res_air = self.insights.calc_vent(vent_hist, 980, (800, 1200, 1600), 420)
        self.assertEqual(res_air["state"], "ventilating")
        self.assertEqual(res_air["rate_ppm_per_h"], -240)
        self.assertEqual(res_air["level"], "bad")
        self.assertEqual(res_air["action"], "keep_airing_out")

        # Insufficient CO2 history + high VOC -> high_voc fallback
        res_no_hist = self.insights.calc_vent({"step_s": 60, "co2_ppm": [500, 505]}, 505, voc_index=280)
        self.assertEqual(res_no_hist["state"], "high_voc")
        self.assertIsNone(res_no_hist["rate_ppm_per_h"])
        self.assertEqual(res_no_hist["level"], "poor")
        self.assertEqual(res_no_hist["action"], "ventilate_now")

        # Insufficient CO2 history + normal VOC -> None
        self.assertIsNone(self.insights.calc_vent({"step_s": 60, "co2_ppm": [500, 505]}, 505, voc_index=100))

    def test_calc_comfort_cases(self) -> None:
        # Mold risk with Magnus dew-point fallback
        mold = self.insights.calc_comfort(28.0, 73.0, None)
        self.assertEqual(mold["state"], "mold_risk")
        self.assertEqual(mold["level"], "bad")
        self.assertEqual(mold["advice"], "dehumidify")
        self.assertGreater(mold["dew_point_c"], 22.0)

        # Muggy
        muggy = self.insights.calc_comfort(27.0, 65.0, 20.5)
        self.assertEqual(muggy["state"], "muggy")
        self.assertEqual(muggy["level"], "moderate")

        # Dry air
        dry = self.insights.calc_comfort(22.0, 24.0, 1.0)
        self.assertEqual(dry["state"], "dry_air")
        self.assertEqual(dry["level"], "poor")

        # Warm / Cool / Comfortable
        self.assertEqual(self.insights.calc_comfort(28.0, 50.0, 16.5)["state"], "warm")
        self.assertEqual(self.insights.calc_comfort(16.5, 50.0, 6.0)["state"], "cool")
        comf = self.insights.calc_comfort(23.5, 52.0, 13.0)
        self.assertEqual(comf["state"], "comfortable")
        self.assertEqual(comf["level"], "good")

    def test_calc_week_co2_and_voc(self) -> None:
        hist_7d = {
            "step_s": 1800,
            "end": 1763900000,
            "co2_ppm": [500, 600, 1300, 1400, 800, 700],
            "voc_index": [100, 120, 260, 310, 270, 110],
        }
        wk = self.insights.calc_week(hist_7d, (800, 1200, 1600), (150, 250, 400))
        self.assertIsNotNone(wk)
        self.assertEqual(wk["co2_min_ppm"], 500)
        self.assertEqual(wk["co2_max_ppm"], 1400)
        self.assertEqual(wk["co2_avg_ppm"], 883)
        self.assertEqual(wk["co2_hours_over_threshold"], 1.0)
        self.assertTrue(wk["has_voc"])
        self.assertEqual(wk["voc_max"], 310)
        self.assertEqual(wk["voc_hours_over_threshold"], 1.5)
        self.assertEqual(wk["level"], "moderate")
        self.assertTrue(wk["daily_breakdown"])
        self.assertIn("voc_max", wk["daily_breakdown"][0])

        # CO2-only history without timestamps
        wk_no_voc = self.insights.calc_week({"step_s": 1800, "co2_ppm": [500, 700, 900]})
        self.assertFalse(wk_no_voc["has_voc"])
        self.assertIsNone(wk_no_voc["voc_max"])
        self.assertEqual(wk_no_voc["daily_breakdown"][0]["date"], "0-24h")

    def test_coordinator_and_insight_sensors_end_to_end(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock

        async def run() -> None:
            status = {
                "device": {"id": "a1b2c3", "name": "Living Room", "fw_version": "2.1.1", "auth": False},
                "readings": {
                    "co2_ppm": 1020,
                    "temperature_c": 28.0,
                    "humidity_rh": 73.0,
                    "dew_point_c": 22.8,
                    "voc_index": 290,
                    "level": "poor",
                },
                "network": {"rssi": -54},
            }
            hist_1h = {"step_s": 60, "co2_ppm": [900 + i * 4 for i in range(31)]}
            hist_7d = {
                "step_s": 1800,
                "end": 1763900000,
                "co2_ppm": [500, 700, 900, 1300, 1400, 600],
                "voc_index": [100, 110, 280, 320, 120, 100],
            }
            cfg = {"co2_t1": 800, "co2_t2": 1200, "co2_t3": 1600, "voc_t1": 150, "voc_t2": 250, "voc_t3": 400}

            client = SimpleNamespace(
                host="192.168.1.42",
                has_password=False,
                async_get_status=AsyncMock(return_value=status),
                async_get_ota=AsyncMock(return_value={"auto": {"last_result": "up_to_date"}}),
                async_get_history=AsyncMock(
                    side_effect=lambda *, minutes, step: hist_1h if minutes == 60 else hist_7d
                ),
                async_get_config=AsyncMock(return_value=cfg),
            )
            entry = SimpleNamespace(entry_id="e1", unique_id="a1b2c3", data={"host": "192.168.1.42"}, options={})
            coord = self.coord_mod.StuffyMeterCoordinator(object(), entry, client)

            await coord.async_refresh()
            self.assertTrue(coord.last_update_success)
            self.assertEqual(client.async_get_history.await_count, 2)
            self.assertEqual(client.async_get_config.await_count, 1)

            # Second poll immediately after uses cached history & config
            await coord.async_refresh()
            self.assertEqual(client.async_get_history.await_count, 2)
            self.assertEqual(client.async_get_config.await_count, 1)

            by_key = {
                d.key: self.sensor_mod.StuffyMeterSensor(coord, d)
                for d in self.sensor_mod.BASE_SENSORS
            }
            self.assertEqual(by_key["ventilation_trend"].native_value, "rising")
            self.assertEqual(by_key["ventilation_trend"].icon, "mdi:trending-up")
            self.assertEqual(by_key["ventilation_trend"].extra_state_attributes["action"], "ventilate_now")
            self.assertEqual(by_key["co2_trend_rate"].native_value, 240)

            self.assertEqual(by_key["comfort_mold"].native_value, "mold_risk")
            self.assertEqual(by_key["comfort_mold"].icon, "mdi:water-alert")
            self.assertEqual(by_key["comfort_mold"].extra_state_attributes["advice"], "dehumidify")

            self.assertEqual(by_key["co2_7d_avg"].native_value, 900)
            self.assertEqual(by_key["co2_7d_avg"].extra_state_attributes["voc_max"], 320)
            self.assertTrue(by_key["co2_7d_avg"].extra_state_attributes["daily_breakdown"])

            # Failing /api/history on older firmware must never fail coordinator refresh
            coord._history_1h_fetched_at = 0.0
            client.async_get_history = AsyncMock(side_effect=self.api.StuffyMeterApiError(404, "not found"))
            await coord.async_refresh()
            self.assertTrue(coord.last_update_success)

        asyncio.run(run())

    def test_calc_week_explicit_tz_and_coordinator_tz(self) -> None:
        from datetime import datetime, timedelta, timezone
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from homeassistant.util import dt as dt_util

        # 2026-06-01 22:00:00 UTC is 2026-06-02 06:00 in UTC+8 and 2026-06-01 18:00 in UTC-4
        end_ts = int(datetime(2026, 6, 1, 22, 0, 0, tzinfo=timezone.utc).timestamp())
        hist_7d = {
            "step_s": 1800,
            "end": end_ts,
            "co2_ppm": [500, 600, 700],
        }
        wk_plus8 = self.insights.calc_week(hist_7d, tz=timezone(timedelta(hours=8)))
        wk_minus4 = self.insights.calc_week(hist_7d, tz=timezone(timedelta(hours=-4)))
        self.assertEqual(wk_plus8["daily_breakdown"][0]["date"], "2026-06-02")
        self.assertEqual(wk_minus4["daily_breakdown"][0]["date"], "2026-06-01")

        # Verify coordinator passes dt_util.get_default_time_zone() to compute_insights
        prev_tz = dt_util.DEFAULT_TIME_ZONE
        try:
            dt_util.DEFAULT_TIME_ZONE = timezone(timedelta(hours=8))
            client = SimpleNamespace(
                host="192.168.1.42",
                has_password=False,
                async_get_status=AsyncMock(
                    return_value={
                        "device": {"id": "a1b2c3", "fw_version": "2.1.2"},
                        "readings": {"co2_ppm": 650},
                    }
                ),
                async_get_ota=AsyncMock(return_value={"auto": {"last_result": "up_to_date"}}),
                async_get_history=AsyncMock(
                    side_effect=lambda *, minutes, step: hist_7d if minutes > 60 else {}
                ),
            )
            entry = SimpleNamespace(
                entry_id="e-tz", unique_id="a1b2c3", data={"host": "192.168.1.42"}, options={}
            )
            coord = self.coord_mod.StuffyMeterCoordinator(object(), entry, client)
            asyncio.run(coord.async_refresh())
            self.assertEqual(
                coord.insights["week"]["daily_breakdown"][0]["date"], "2026-06-02"
            )
        finally:
            dt_util.DEFAULT_TIME_ZONE = prev_tz

    def test_calc_vent_staleness_and_above_t2(self) -> None:
        rising_hist = {
            "step_s": 60,
            "end": 1763900000,
            "co2_ppm": [1100 + i * 5 for i in range(31)],
        }
        # Fresh history (300 s old <= 600 s) with cur >= t2 (1300 >= 1200)
        res_fresh = self.insights.calc_vent(
            rising_hist, 1300, (800, 1200, 1600), 100, now_ts=1763900300
        )
        self.assertIsNotNone(res_fresh)
        self.assertEqual(res_fresh["state"], "rising")
        self.assertEqual(res_fresh["action"], "ventilate_now")
        self.assertEqual(res_fresh["level"], "bad")
        self.assertIsNone(res_fresh["eta_minutes"])
        self.assertIsNone(res_fresh["target_ppm"])

        # Stale history (601 s old > 600 s) rejected when VOC is normal
        res_stale = self.insights.calc_vent(
            rising_hist, 1300, (800, 1200, 1600), 100, now_ts=1763900601
        )
        self.assertIsNone(res_stale)

        # Stale history still surfaces high_voc when live VOC is poor/bad
        res_stale_voc = self.insights.calc_vent(
            rising_hist, 600, (800, 1200, 1600), 320, now_ts=1763900900
        )
        self.assertEqual(res_stale_voc["state"], "high_voc")
        self.assertEqual(res_stale_voc["action"], "ventilate_now")

        # Via compute_insights using status.device.unix_time
        ins = self.insights.compute_insights(
            {
                "device": {"unix_time": 1763901000},
                "readings": {"co2_ppm": 1300, "voc_index": 100},
            },
            rising_hist,
            None,
        )
        self.assertIsNone(ins["vent"])

    def test_recorder_db_bloat_and_uptime_timestamp(self) -> None:
        from datetime import datetime, timedelta, timezone
        from types import SimpleNamespace
        from unittest.mock import AsyncMock
        from homeassistant.components.sensor import SensorDeviceClass
        from homeassistant.util import dt as dt_util

        self.assertEqual(
            self.sensor_mod.StuffyMeterSensor._unrecorded_attributes,
            frozenset({"daily_breakdown"}),
        )

        by_desc = {d.key: d for d in self.sensor_mod.BASE_SENSORS}
        self.assertFalse(by_desc["heap_free"].entity_registry_enabled_default)
        self.assertEqual(
            by_desc["abs_humidity"].device_class,
            SensorDeviceClass.ABSOLUTE_HUMIDITY,
        )
        self.assertEqual(by_desc["uptime"].device_class, SensorDeviceClass.TIMESTAMP)
        self.assertIsNone(by_desc["uptime"].native_unit_of_measurement)
        self.assertIsNone(by_desc["uptime"].state_class)

        # Verify volatile duplicated attributes are omitted from enum sensors
        status = {
            "device": {
                "id": "a1b2c3",
                "fw_version": "2.1.2",
                "boot_count": 4,
                "uptime_s": 3600,
            },
            "readings": {
                "co2_ppm": 950,
                "temperature_c": 28.0,
                "humidity_rh": 73.0,
                "dew_point_c": 22.8,
                "voc_index": 290,
            },
        }
        hist_1h = {"step_s": 60, "co2_ppm": [800 + i * 4 for i in range(31)]}
        client = SimpleNamespace(
            host="192.168.1.42",
            has_password=False,
            async_get_status=AsyncMock(return_value=status),
            async_get_ota=AsyncMock(return_value={"auto": {"last_result": "up_to_date"}}),
            async_get_history=AsyncMock(
                side_effect=lambda *, minutes, step: hist_1h if minutes == 60 else {}
            ),
        )
        entry = SimpleNamespace(
            entry_id="e-rec", unique_id="a1b2c3", data={"host": "192.168.1.42"}, options={}
        )
        coord = self.coord_mod.StuffyMeterCoordinator(object(), entry, client)

        fixed_now = [datetime(2026, 6, 1, 12, 0, 0, tzinfo=timezone.utc)]
        orig_utcnow = dt_util.utcnow
        dt_util.utcnow = lambda: fixed_now[0]
        try:
            asyncio.run(coord.async_refresh())
            vent_sensor = self.sensor_mod.StuffyMeterSensor(
                coord, by_desc["ventilation_trend"]
            )
            comfort_sensor = self.sensor_mod.StuffyMeterSensor(
                coord, by_desc["comfort_mold"]
            )
            uptime_sensor = self.sensor_mod.StuffyMeterSensor(coord, by_desc["uptime"])

            v_attrs = vent_sensor.extra_state_attributes
            self.assertNotIn("rate_ppm_per_h", v_attrs)
            self.assertNotIn("voc_index", v_attrs)
            self.assertIn("level", v_attrs)
            self.assertIn("action", v_attrs)
            self.assertIn("voc_level", v_attrs)

            c_attrs = comfort_sensor.extra_state_attributes
            self.assertEqual(set(c_attrs.keys()), {"level", "advice"})

            # Uptime timestamp stays exact across normal polls (even with 2s jitter)
            t_boot_1 = uptime_sensor.native_value
            self.assertEqual(
                t_boot_1, datetime(2026, 6, 1, 11, 0, 0, tzinfo=timezone.utc)
            )
            fixed_now[0] = datetime(2026, 6, 1, 12, 0, 17, tzinfo=timezone.utc)
            coord.data = {
                **status,
                "device": {**status["device"], "uptime_s": 3615},
            }
            self.assertEqual(uptime_sensor.native_value, t_boot_1)

            # Reboot (boot_count increments) updates boot timestamp
            fixed_now[0] = datetime(2026, 6, 1, 12, 5, 0, tzinfo=timezone.utc)
            coord.data = {
                **status,
                "device": {**status["device"], "boot_count": 5, "uptime_s": 10},
            }
            self.assertEqual(
                uptime_sensor.native_value,
                datetime(2026, 6, 1, 12, 4, 50, tzinfo=timezone.utc),
            )

            # Missing uptime_s returns None cleanly
            coord.data = {"device": {"id": "a1b2c3"}}
            self.assertIsNone(uptime_sensor.native_value)
        finally:
            dt_util.utcnow = orig_utcnow

    def test_coordinator_retry_backoff_reauth_and_device_registry_sync(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock
        from homeassistant.helpers import device_registry as dr

        dev_reg = MagicMock()
        dev_reg.async_get_device_by_identifier.return_value = None
        dr.async_get.return_value = dev_reg

        status = {
            "device": {"id": "a1b2c3", "name": "Office", "fw_version": "2.1.2", "auth": True},
            "readings": {"co2_ppm": 600},
        }
        client = SimpleNamespace(
            host="192.168.1.42",
            has_password=True,
            async_get_status=AsyncMock(return_value=status),
            async_get_ota=AsyncMock(return_value={"auto": {"last_result": "up_to_date"}}),
            async_get_history=AsyncMock(
                side_effect=self.api.StuffyMeterConnectionError("timeout")
            ),
            async_get_config=AsyncMock(
                side_effect=self.api.StuffyMeterAuthError("HTTP 401")
            ),
        )
        entry = SimpleNamespace(
            entry_id="e-retry",
            unique_id="a1b2c3",
            data={"host": "192.168.1.42"},
            options={},
            async_start_reauth=MagicMock(),
        )
        hass = object()
        coord = self.coord_mod.StuffyMeterCoordinator(hass, entry, client)

        async def run() -> None:
            await coord.async_refresh()
            self.assertTrue(coord.last_update_success)
            # Failed history_7d and config use 60 s retry backoff instead of 900s / 3600s
            self.assertEqual(coord._history_7d_interval, self.const.INSIGHTS_RETRY_INTERVAL)
            self.assertEqual(coord._config_interval, self.const.INSIGHTS_RETRY_INTERVAL)
            # Auth error triggered reauth once without breaking polling
            entry.async_start_reauth.assert_called_once_with(hass)
            # dev_entry was None on first refresh, so _last_device_name must NOT be cached yet
            self.assertIsNone(coord._last_device_name)

            # Once device appears in registry with stale name/fw, next refresh updates it
            dev_entry = SimpleNamespace(id="dev-9", name="Old Name", sw_version="2.0.0")
            dev_reg.async_get_device_by_identifier.return_value = dev_entry
            # Also simulate recovery of history/config
            coord._history_7d_fetched_at = 0.0
            coord._config_fetched_at = 0.0
            client.async_get_history = AsyncMock(return_value={"step_s": 1800, "co2_ppm": [500, 600]})
            client.async_get_config = AsyncMock(return_value={"co2_t2": 1200})
            await coord.async_refresh()

            dev_reg.async_update_device.assert_called_once_with(
                "dev-9", name="Office", sw_version="2.1.2"
            )
            self.assertEqual(coord._last_device_name, "Office")
            self.assertEqual(coord._history_7d_interval, self.const.HISTORY_7D_REFRESH_INTERVAL)
            self.assertEqual(coord._config_interval, self.const.CONFIG_REFRESH_INTERVAL)

        asyncio.run(run())


class TestPlatformsAndConfigFlow(unittest.TestCase):
    """Test entry.runtime_data setup, binary_sensor, and config/options/reauth flows."""

    def setUp(self) -> None:
        self._saved_mods = _install_ha_stubs()
        pkg = type(sys)("custom_components.stuffy_meter")
        pkg.__path__ = [str(COMP)]
        sys.modules.setdefault("custom_components", type(sys)("custom_components"))
        sys.modules["custom_components.stuffy_meter"] = pkg
        self.const = _load_module("custom_components.stuffy_meter.const", COMP / "const.py")
        self.api = _load_module("custom_components.stuffy_meter.api", COMP / "api.py")
        self.insights = _load_module(
            "custom_components.stuffy_meter.insights", COMP / "insights.py"
        )
        self.coord_mod = _load_module(
            "custom_components.stuffy_meter.coordinator", COMP / "coordinator.py"
        )
        self.entity_mod = _load_module(
            "custom_components.stuffy_meter.entity", COMP / "entity.py"
        )
        self.init_mod = _load_module(
            "custom_components.stuffy_meter.__init__", COMP / "__init__.py"
        )
        self.sensor_mod = _load_module(
            "custom_components.stuffy_meter.sensor", COMP / "sensor.py"
        )
        self.bs_mod = _load_module(
            "custom_components.stuffy_meter.binary_sensor", COMP / "binary_sensor.py"
        )
        self.button_mod = _load_module(
            "custom_components.stuffy_meter.button", COMP / "button.py"
        )
        self.update_mod = _load_module(
            "custom_components.stuffy_meter.update", COMP / "update.py"
        )
        self.cf_mod = _load_module(
            "custom_components.stuffy_meter.config_flow", COMP / "config_flow.py"
        )

    def tearDown(self) -> None:
        for mod_name in list(sys.modules):
            if mod_name.startswith("custom_components.stuffy_meter"):
                sys.modules.pop(mod_name, None)
        _restore_modules(self._saved_mods)

    def test_runtime_data_setup_and_binary_sensors(self) -> None:
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch
        from homeassistant.helpers import device_registry as dr

        dr.async_get.return_value = MagicMock(
            async_get_device_by_identifier=MagicMock(return_value=None)
        )

        bat_status = {
            "device": {"id": "bat001", "name": "Bedroom", "board": "4.1", "fw_version": "2.1.2"},
            "readings": {"co2_ppm": 550},
            "power": {"usb": True, "battery_present": True, "battery_pct": 92, "battery_v": 4.12, "charging": False},
        }
        usb_status = {
            "device": {"id": "usb001", "name": "Desk", "board": "3.2", "fw_version": "2.1.2"},
            "readings": {"co2_ppm": 610},
        }

        async def run() -> None:
            hass = SimpleNamespace(
                config_entries=SimpleNamespace(
                    async_forward_entry_setups=AsyncMock(return_value=True),
                    async_unload_platforms=AsyncMock(return_value=True),
                    async_reload=AsyncMock(),
                )
            )
            entry = SimpleNamespace(
                entry_id="e-bat",
                unique_id="bat001",
                data={"host": "192.168.1.50", "password": ""},
                options={},
                runtime_data=None,
                async_on_unload=MagicMock(),
                add_update_listener=MagicMock(return_value=lambda: None),
            )

            with (
                patch.object(self.api.StuffyMeterClient, "async_get_status", AsyncMock(return_value=bat_status)),
                patch.object(self.api.StuffyMeterClient, "async_get_ota", AsyncMock(return_value={"auto": {}})),
                patch.object(self.api.StuffyMeterClient, "async_get_history", AsyncMock(return_value={})),
            ):
                ok = await self.init_mod.async_setup_entry(hass, entry)
                self.assertTrue(ok)
                self.assertIsInstance(entry.runtime_data, self.coord_mod.StuffyMeterCoordinator)

            # All 4 platforms load from entry.runtime_data
            added_bs: list = []
            await self.bs_mod.async_setup_entry(hass, entry, added_bs.extend)
            self.assertEqual(len(added_bs), 2)
            bs_by_key = {e.entity_description.key: e for e in added_bs}
            self.assertTrue(bs_by_key["power"].is_on)
            self.assertFalse(bs_by_key["battery_charging"].is_on)

            added_sensors: list = []
            await self.sensor_mod.async_setup_entry(hass, entry, added_sensors.extend)
            self.assertEqual(
                len(added_sensors),
                len(self.sensor_mod.BASE_SENSORS) + len(self.sensor_mod.BATTERY_SENSORS),
            )

            added_btns: list = []
            await self.button_mod.async_setup_entry(hass, entry, added_btns.extend)
            self.assertEqual(len(added_btns), len(self.button_mod.BUTTONS))

            added_upd: list = []
            await self.update_mod.async_setup_entry(hass, entry, added_upd.extend)
            self.assertEqual(len(added_upd), 1)

            # USB edition adds zero binary sensors
            entry.runtime_data.data = usb_status
            added_usb_bs: list = []
            await self.bs_mod.async_setup_entry(hass, entry, added_usb_bs.extend)
            self.assertEqual(added_usb_bs, [])

            # Unload returns True cleanly
            self.assertTrue(await self.init_mod.async_unload_entry(hass, entry))

        asyncio.run(run())

    def test_config_flow_zeroconf_options_reconfigure_reauth(self) -> None:
        import ipaddress
        from types import SimpleNamespace
        from unittest.mock import AsyncMock, MagicMock, patch
        from homeassistant.config_entries import AbortFlow
        import voluptuous as vol

        def _password_marker_has_default(schema) -> bool:
            for marker in schema.schema:
                if getattr(marker, "schema", None) == "password":
                    return marker.default is not vol.UNDEFINED
            return False

        async def run() -> None:
            # 1. Zeroconf: unreachable IP aborts BEFORE overwriting existing entry's CONF_HOST
            existing_local = SimpleNamespace(
                entry_id="e-local",
                unique_id="a1b2c3",
                title="Living Room",
                data={"host": "stuffymeter-a1b2c3.local", "password": "oldpw"},
                options={},
            )
            flow = self.cf_mod.StuffyMeterConfigFlow()
            flow.hass = object()
            flow._async_current_entries = lambda: [existing_local]

            disc = SimpleNamespace(
                ip_address=ipaddress.ip_address("192.168.1.99"),
                host="192.168.1.99",
                port=80,
                properties={"id": "a1b2c3"},
            )
            with patch.object(
                self.cf_mod,
                "_validate_monitor",
                AsyncMock(return_value=({}, {"base": "cannot_connect"})),
            ):
                res = await flow.async_step_zeroconf(disc)
            self.assertEqual(res["type"], "abort")
            self.assertEqual(res["reason"], "cannot_connect")
            # Working .local host was NOT overwritten
            self.assertEqual(existing_local.data["host"], "stuffymeter-a1b2c3.local")

            # 2. Zeroconf: even when IP IS reachable, do NOT overwrite existing .local host
            valid_status = {
                "device": {"id": "a1b2c3", "name": "Living Room"},
                "readings": {"co2_ppm": 600},
            }
            with patch.object(
                self.cf_mod,
                "_validate_monitor",
                AsyncMock(return_value=(valid_status, {})),
            ):
                with self.assertRaises(AbortFlow) as abort_ctx:
                    await flow.async_step_zeroconf(disc)
            self.assertEqual(abort_ctx.exception.reason, "already_configured")
            self.assertEqual(existing_local.data["host"], "stuffymeter-a1b2c3.local")

            # 3. Zeroconf: reachable IP DOES update existing IP-based entry
            existing_ip = SimpleNamespace(
                entry_id="e-ip",
                unique_id="a1b2c3",
                title="Living Room",
                data={"host": "192.168.1.40", "password": "oldpw"},
                options={},
            )
            flow._async_current_entries = lambda: [existing_ip]
            with patch.object(
                self.cf_mod,
                "_validate_monitor",
                AsyncMock(return_value=(valid_status, {})),
            ):
                with self.assertRaises(AbortFlow) as abort_ctx:
                    await flow.async_step_zeroconf(disc)
            self.assertEqual(abort_ctx.exception.reason, "already_configured")
            self.assertEqual(existing_ip.data["host"], "192.168.1.99")

            # 4. OptionsFlow: NEVER prefills plaintext password; stores new pw in entry.data
            opt_entry = SimpleNamespace(
                entry_id="e-opt",
                unique_id="a1b2c3",
                title="Living Room",
                data={"host": "192.168.1.42", "password": "secret-password"},
                options={"scan_interval": 15},
            )
            opt_flow = self.cf_mod.StuffyMeterOptionsFlow()
            opt_flow.config_entry = opt_entry
            updated_data_calls: list[dict] = []

            def fake_update_entry(entry, *, data=None):
                if data is not None:
                    entry.data = dict(data)
                    updated_data_calls.append(dict(data))

            opt_flow.hass = SimpleNamespace(
                config_entries=SimpleNamespace(async_update_entry=fake_update_entry)
            )
            form = await opt_flow.async_step_init(None)
            self.assertEqual(form["type"], "form")
            self.assertFalse(_password_marker_has_default(form["data_schema"]))

            # Blank password keeps existing entry.data password and stores only scan_interval in options
            res = await opt_flow.async_step_init({"scan_interval": 30})
            self.assertEqual(res["type"], "create_entry")
            self.assertEqual(res["data"], {"scan_interval": 30})
            self.assertNotIn("password", res["data"])
            self.assertEqual(opt_entry.data["password"], "secret-password")

            # Entering a new password validates and updates entry.data (NOT options)
            with patch.object(
                self.cf_mod,
                "_validate_monitor",
                AsyncMock(return_value=(valid_status, {})),
            ) as mock_val:
                res = await opt_flow.async_step_init(
                    {"scan_interval": 20, "password": "new-password"}
                )
            mock_val.assert_awaited_once_with(
                opt_flow.hass, "192.168.1.42", "new-password", require_auth=True
            )
            self.assertEqual(res["data"], {"scan_interval": 20})
            self.assertEqual(opt_entry.data["password"], "new-password")

            # 5. Reconfigure: NEVER prefills plaintext password; blank keeps existing password
            rec_entry = SimpleNamespace(
                entry_id="e-rec",
                unique_id="a1b2c3",
                title="Living Room",
                data={"host": "192.168.1.42", "password": "kept-password"},
                options={"scan_interval": 15},
            )
            flow._get_reconfigure_entry = lambda: rec_entry
            rec_form = await flow.async_step_reconfigure(None)
            self.assertFalse(_password_marker_has_default(rec_form["data_schema"]))

            with patch.object(
                self.cf_mod,
                "_validate_monitor",
                AsyncMock(return_value=(valid_status, {})),
            ) as mock_val:
                await flow.async_step_reconfigure({"host": "192.168.1.77"})
            mock_val.assert_awaited_once_with(flow.hass, "192.168.1.77", "kept-password")
            self.assertEqual(rec_entry.data["host"], "192.168.1.77")
            self.assertEqual(rec_entry.data["password"], "kept-password")

            # 6. Reauth flow: validates with require_auth=True, updates entry.data, strips legacy options password
            reauth_entry = SimpleNamespace(
                entry_id="e-reauth",
                unique_id="a1b2c3",
                title="Living Room",
                data={"host": "192.168.1.42", "password": "stale"},
                options={"scan_interval": 15, "password": "stale"},
            )
            flow._get_reauth_entry = lambda: reauth_entry
            reauth_form = await flow.async_step_reauth(reauth_entry.data)
            self.assertEqual(reauth_form["type"], "form")
            self.assertEqual(reauth_form["step_id"], "reauth_confirm")
            self.assertFalse(_password_marker_has_default(reauth_form["data_schema"]))

            # Invalid auth shows error
            with patch.object(
                self.cf_mod,
                "_validate_monitor",
                AsyncMock(return_value=({}, {"base": "invalid_auth"})),
            ):
                bad_res = await flow.async_step_reauth_confirm({"password": "wrong"})
            self.assertEqual(bad_res["type"], "form")
            self.assertEqual(bad_res["errors"], {"base": "invalid_auth"})

            # Valid new password updates entry.data and strips password from options
            with patch.object(
                self.cf_mod,
                "_validate_monitor",
                AsyncMock(return_value=(valid_status, {})),
            ) as mock_val:
                ok_res = await flow.async_step_reauth_confirm({"password": "fresh-pw"})
            mock_val.assert_awaited_once_with(
                flow.hass, "192.168.1.42", "fresh-pw", require_auth=True
            )
            self.assertEqual(ok_res["type"], "abort")
            self.assertEqual(reauth_entry.data["password"], "fresh-pw")
            self.assertNotIn("password", reauth_entry.options)
            self.assertEqual(reauth_entry.options["scan_interval"], 15)

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
