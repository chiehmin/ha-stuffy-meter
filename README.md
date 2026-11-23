# Stuffy Meter for Home Assistant

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=chiehmin&repository=ha-stuffy-meter&category=integration)

Custom [Home Assistant](https://www.home-assistant.io/) integration for the **Stuffy Meter** CO₂ and VOC air-quality monitor — **100% local, zero cloud, and no MQTT broker required**.

- **Automatic discovery**: Detects Stuffy Meter devices on your LAN via mDNS (`_stuffymeter._tcp` and legacy `_smartaq._tcp`).
- **Local polling**: Polls `GET /api/status` directly over your local network (default every 15 seconds, configurable 5–300 s).
- **No password required for sensors**: Reading sensors, seeing available updates, and triggering **Identify** work out of the box without entering the admin password. The admin password is only needed if you want to trigger **Reboot**, **Check for updates**, or **Firmware** OTA installation from Home Assistant (when an admin password is set on the monitor).
- **In-app OTA firmware updates**: Native Home Assistant `update` entity surfaces available firmware releases in **Settings → Updates** with release notes links, live download progress (0–100%), and one-click OTA installation directly from the Home Assistant UI.
- **Supports both hardware editions**: Automatically exposes battery and USB-power entities for Battery edition (rev 4.x) boards alongside USB edition (rev 3.2) boards.
- **Live name & firmware sync**: Renaming a monitor (`device_name`, e.g. `客廳` / `Living Room`) or upgrading firmware via OTA automatically updates its device metadata in Home Assistant.

> Already running an MQTT broker? Stuffy Meter also supports built-in MQTT Auto-Discovery on the device itself. Use this HACS integration if you want a zero-broker setup!

---

## Installation (HACS)

### Option 1: One-click button

Click the **Open HACS Repository** badge at the top of this page, click **Download**, and restart Home Assistant.

### Option 2: Manual custom repository in HACS

1. Open **HACS** in Home Assistant.
2. Click the three-dot menu (`⋮`) in the top right → **Custom repositories**.
3. Add `https://github.com/chiehmin/ha-stuffy-meter` with category **Integration**.
4. Search for **Stuffy Meter**, click **Download**, and restart Home Assistant.

### Option 3: Manual copy (without HACS)

Copy `custom_components/stuffy_meter` into your Home Assistant `config/custom_components/stuffy_meter` directory and restart Home Assistant.

---

## Setup

- **Automatic (mDNS)**: After restarting Home Assistant, go to **Settings → Devices & services**. Any Stuffy Meter on the same LAN appears automatically under **Discovered** — click **Configure** and confirm.
- **Manual**: Go to **Settings → Devices & services → Add Integration**, search for **Stuffy Meter**, and enter the monitor's IP address or `.local` hostname (e.g. `stuffymeter-a1b2c3.local`).

---

## Entities

### Sensors (all boards)

| Entity | Unit / Values | Category |
| --- | --- | --- |
| CO₂ | `ppm` | Primary |
| Temperature | `°C` | Primary |
| Humidity | `%` | Primary |
| VOC index | `1–500` | Primary |
| Dew point | `°C` | Primary |
| Absolute humidity | `g/m³` | Primary |
| Air quality | `good` / `moderate` / `poor` / `bad` | Primary |
| Wi-Fi signal | `dBm` | Diagnostic |
| Uptime | `s` | Diagnostic |
| Free heap | `B` | Diagnostic |
| Firmware version | e.g. `1.10.2` or `1.10.2 → 2.0.1` | Diagnostic |

### Firmware Update & Buttons (all boards)

| Entity | Type | Action / Details | Category |
| --- | --- | --- | --- |
| Firmware | Update | Shows installed vs. latest firmware version, links to release notes, and installs OTA updates with live progress (`GET /api/ota`, `POST /api/ota/check`) | Config |
| Check for updates | Button | Immediately checks the monitor's OTA manifest for a newer firmware release (`POST /api/ota/check`, requires optional admin password) | Config |
| Identify | Button | Blinks LED and flashes screen for 10 s (`POST /api/identify`) | Diagnostic |
| Reboot | Button | Reboots the monitor (`POST /api/reboot`, requires optional admin password) | Config |

### Battery edition (rev 4.x only)

| Entity | Type | Unit / Class |
| --- | --- | --- |
| Battery | Sensor | `%` |
| Battery voltage | Sensor (Diagnostic) | `V` |
| USB power | Binary sensor | `plug` |
| Battery charging | Binary sensor | `battery_charging` |

---

## 繁體中文快速安裝

1. 點擊本頁上方的 **HACS** 按鈕（或在 HACS 右上角選單 →「自訂儲存庫」加入 `https://github.com/chiehmin/ha-stuffy-meter`，類型選「整合」）。
2. 下載 **Stuffy Meter** 並重新啟動 Home Assistant。
3. 重新啟動後，Home Assistant 會自動透過區域網路 mDNS 探索同網段的 Stuffy Meter，於「設定 → 裝置與服務」點擊設定即可完成（不需架設 MQTT 伺服器，管理員密碼為選填；若偵測器有設定管理員密碼，輸入後即可直接在 Home Assistant 介面執行重新啟動、檢查更新與 OTA 韌體更新）。

## License

MIT — see [LICENSE](LICENSE).
