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
        sensor_src = (COMP / "sensor.py").read_text(encoding="utf-8")
        self.assertIn("SensorDeviceClass.CO2", sensor_src)
        self.assertNotIn("SensorDeviceClass.CARBON_DIOXIDE", sensor_src)
        self.assertIn("UnitOfRatio.PARTS_PER_MILLION", sensor_src)

        coord_src = (COMP / "coordinator.py").read_text(encoding="utf-8")
        self.assertIn("async_get_device_by_identifier", coord_src)

        init_src = (COMP / "__init__.py").read_text(encoding="utf-8")
        self.assertIn("Platform.UPDATE", init_src)

        update_src = (COMP / "update.py").read_text(encoding="utf-8")
        self.assertIn("UpdateDeviceClass.FIRMWARE", update_src)
        self.assertIn("UpdateEntityFeature.INSTALL", update_src)
        self.assertIn("UpdateEntityFeature.PROGRESS", update_src)
        self.assertIn("def update_percentage", update_src)


def _install_ha_stubs() -> dict[str, Any]:
    """Install minimal homeassistant module stubs so integration modules can be unit-tested."""
    from enum import IntFlag, StrEnum
    import types
    from unittest.mock import MagicMock

    saved: dict[str, Any] = {}

    def _register(name: str, mod: types.ModuleType) -> types.ModuleType:
        if name not in saved:
            saved[name] = sys.modules.get(name)
        sys.modules[name] = mod
        return mod

    ha = _register("homeassistant", types.ModuleType("homeassistant"))
    ha.__path__ = []  # type: ignore[attr-defined]

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

    # homeassistant.config_entries
    ce_mod = _register(
        "homeassistant.config_entries",
        types.ModuleType("homeassistant.config_entries"),
    )
    ce_mod.ConfigEntry = object

    # homeassistant.exceptions
    exc_mod = _register(
        "homeassistant.exceptions",
        types.ModuleType("homeassistant.exceptions"),
    )

    class HomeAssistantError(Exception):
        pass

    exc_mod.HomeAssistantError = HomeAssistantError

    # homeassistant.helpers.*
    helpers = _register("homeassistant.helpers", types.ModuleType("homeassistant.helpers"))
    helpers.__path__ = []  # type: ignore[attr-defined]

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

    from dataclasses import dataclass

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

    sens_mod = _register(
        "homeassistant.components.sensor",
        types.ModuleType("homeassistant.components.sensor"),
    )

    class SensorDeviceClass(StrEnum):
        CO2 = "carbon_dioxide"
        TEMPERATURE = "temperature"
        HUMIDITY = "humidity"
        ENUM = "enum"
        SIGNAL_STRENGTH = "signal_strength"
        DURATION = "duration"
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
    """Test StuffyMeterClient against an in-process aiohttp server."""

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

    def test_update_badges_summary_and_firmware_sensor(self) -> None:
        from unittest.mock import MagicMock
        import xml.etree.ElementTree as ET

        sensor_mod = _load_module(
            "custom_components.stuffy_meter.sensor", COMP / "sensor.py"
        )

        # Validate all three SVG data URIs decode to valid SVG XML
        for uri in (
            self.update_mod.UPDATE_PICTURE_AVAILABLE,
            self.update_mod.UPDATE_PICTURE_INSTALLING,
            self.update_mod.UPDATE_PICTURE_UP_TO_DATE,
        ):
            self.assertTrue(uri.startswith("data:image/svg+xml;base64,"))
            svg_xml = base64.b64decode(uri.split(",", 1)[1]).decode("utf-8")
            root = ET.fromstring(svg_xml)
            self.assertTrue(root.tag.endswith("svg"))

        client = MagicMock()
        client.host = "192.168.1.42"
        coord = self.coord_mod.StuffyMeterCoordinator(object(), self._make_entry(), client)
        upd = self.update_mod.StuffyMeterFirmwareUpdate(coord)
        fw_desc = next(s for s in sensor_mod.BASE_SENSORS if s.key == "firmware_version")
        fw_sensor = sensor_mod.StuffyMeterSensor(coord, fw_desc)

        # 1. Up to date
        coord.data = self._make_status(fw_version="1.10.2", available_version="")
        self.assertEqual(upd.entity_picture, self.update_mod.UPDATE_PICTURE_UP_TO_DATE)
        self.assertEqual(upd.icon, "mdi:package-check")
        self.assertIsNone(upd.release_summary)
        self.assertEqual(fw_sensor.native_value, "1.10.2")
        self.assertEqual(fw_sensor.icon, "mdi:package-check")
        self.assertIsNone(fw_sensor.entity_picture)

        # 2. Update available
        coord.data = self._make_status(fw_version="1.10.2", available_version="2.0.1")
        self.assertEqual(upd.entity_picture, self.update_mod.UPDATE_PICTURE_AVAILABLE)
        self.assertEqual(upd.icon, "mdi:package-up")
        self.assertEqual(
            upd.release_summary,
            "2.0.1 is available for update (installed: 1.10.2).",
        )
        self.assertEqual(fw_sensor.native_value, "1.10.2 → 2.0.1")
        self.assertEqual(fw_sensor.icon, "mdi:package-up")
        self.assertEqual(fw_sensor.entity_picture, self.update_mod.UPDATE_PICTURE_AVAILABLE)

        # 3. Installing
        coord.data = self._make_status(
            fw_version="1.10.2",
            ota_state="downloading",
            progress=40,
            available_version="2.0.1",
            new_version="2.0.1",
        )
        self.assertEqual(upd.entity_picture, self.update_mod.UPDATE_PICTURE_INSTALLING)
        self.assertEqual(upd.icon, "mdi:progress-download")


if __name__ == "__main__":
    unittest.main()
