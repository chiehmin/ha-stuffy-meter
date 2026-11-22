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
            api.normalize_host("  stuffymeter-a1b2c3.local/  "),
            "stuffymeter-a1b2c3.local",
        )

        expected_auth = "Basic " + base64.b64encode(b"admin:secret123").decode()
        identify_payloads: list[dict] = []
        reboot_calls = 0

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

        async def run() -> None:
            app = web.Application()
            app.router.add_get("/api/status", handle_status)
            app.router.add_get("/api/config", handle_config)
            app.router.add_post("/api/identify", handle_identify)
            app.router.add_post("/api/reboot", handle_reboot)
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

                    await pub.async_identify(15)
                    self.assertEqual(identify_payloads, [{"seconds": 15}])

                    with self.assertRaises(api.StuffyMeterAuthError):
                        await pub.async_reboot()

                    # Authenticated access
                    authed = api.StuffyMeterClient(
                        session, f"127.0.0.1:{port}", password="secret123"
                    )
                    cfg = await authed.async_verify_auth()
                    self.assertEqual(cfg["device_name"], "客廳")
                    await authed.async_reboot()
                    self.assertEqual(reboot_calls, 1)
            finally:
                await runner.cleanup()

        asyncio.run(run())


if __name__ == "__main__":
    unittest.main()
