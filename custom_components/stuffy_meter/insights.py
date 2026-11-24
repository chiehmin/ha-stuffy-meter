"""Air-quality Insights calculations matching the Stuffy Meter web dashboard.

Pure standard-library logic (no Home Assistant imports) so it can be unit-tested
directly and kept in parity with ``web/src/app.js`` (``calcVent``,
``calcComfort``, ``calcWeek``).
"""

from __future__ import annotations

from datetime import datetime, timezone
import math
from typing import Any

DEFAULT_CO2_THRESHOLDS: tuple[int, int, int] = (800, 1200, 1600)
DEFAULT_VOC_THRESHOLDS: tuple[int, int, int] = (150, 250, 400)

LEVELS: tuple[str, ...] = ("unknown", "good", "moderate", "poor", "bad")

VENTILATION_STATES: list[str] = ["steady", "rising", "ventilating", "high_voc"]
COMFORT_STATES: list[str] = [
    "comfortable",
    "mold_risk",
    "muggy",
    "dry_air",
    "warm",
    "cool",
]


def _is_num(val: Any) -> bool:
    return isinstance(val, (int, float)) and not isinstance(val, bool) and math.isfinite(val)


def _js_round(val: float) -> int:
    """Match JS Math.round (round half toward +infinity) for consistent results."""
    return math.floor(val + 0.5)


def threshold_level(val: float, thresholds: tuple[float, float, float]) -> str:
    """Classify a CO2 or VOC value against (t1, t2, t3)."""
    t1, t2, t3 = thresholds
    if val >= t3:
        return "bad"
    if val >= t2:
        return "poor"
    if val >= t1:
        return "moderate"
    return "good"


def max_level(a: str, b: str) -> str:
    """Return the worse of two air-quality severity levels."""
    ia = LEVELS.index(a) if a in LEVELS else 0
    ib = LEVELS.index(b) if b in LEVELS else 0
    return LEVELS[max(ia, ib)]


def extract_thresholds(
    config: dict[str, Any] | None,
) -> tuple[tuple[int, int, int], tuple[int, int, int]]:
    """Read (co2_t1..3, voc_t1..3) from /api/config or return defaults."""
    if not isinstance(config, dict):
        return DEFAULT_CO2_THRESHOLDS, DEFAULT_VOC_THRESHOLDS

    def _triple(prefix: str, default: tuple[int, int, int]) -> tuple[int, int, int]:
        vals: list[int] = []
        for idx, fallback in enumerate(default, start=1):
            raw = config.get(f"{prefix}{idx}")
            vals.append(int(raw) if _is_num(raw) else fallback)
        return (vals[0], vals[1], vals[2])

    return (
        _triple("co2_t", DEFAULT_CO2_THRESHOLDS),
        _triple("voc_t", DEFAULT_VOC_THRESHOLDS),
    )


def calc_vent(
    history_1h: dict[str, Any] | None,
    cur_co2: float | None,
    co2_thr: tuple[float, float, float] = DEFAULT_CO2_THRESHOLDS,
    voc_index: float | None = None,
    voc_thr: tuple[float, float, float] = DEFAULT_VOC_THRESHOLDS,
) -> dict[str, Any] | None:
    """Compute VOC-aware ventilation trend from 1-hour CO2 history and live VOC.

    Matches ``calcVent()`` in Stuffy Meter's ``web/src/app.js``:
    - Uses OLS linear regression over the most recent 30 minutes of 1h CO2 samples
      (requiring >= 10 valid samples spanning >= 15 min, latest sample <= 10 min old).
    - Escalates severity and action when live VOC index is elevated/high, and
      still surfaces ``high_voc`` before 15 minutes of CO2 history accumulate.
    """
    has_voc = _is_num(voc_index)
    voc_val = _js_round(float(voc_index)) if has_voc else None
    voc_level = threshold_level(voc_val, voc_thr) if voc_val is not None else "good"
    voc_high = voc_level in ("poor", "bad")

    arr = history_1h.get("co2_ppm") if isinstance(history_1h, dict) else None
    if not isinstance(arr, list):
        arr = []
    n = len(arr)

    raw_step = history_1h.get("step_s") if isinstance(history_1h, dict) else None
    step = float(raw_step) if _is_num(raw_step) and raw_step > 0 else 60.0
    win = min(n, max(1, _js_round(1800.0 / step))) if n else 0

    m = 0
    x0 = -1.0
    x1 = 0.0
    last = -1
    y1 = 0.0
    sx = sy = sxx = sxy = 0.0
    start = n - win
    for i in range(start, n):
        v = arr[i]
        if _is_num(v):
            x = (i - start) * step / 60.0
            y = float(v)
            if x0 < 0:
                x0 = x
            x1 = x
            last = i
            y1 = y
            m += 1
            sx += x
            sy += y
            sxx += x * x
            sxy += x * y

    den = m * sxx - sx * sx
    if (
        not n
        or m < 10
        or (x1 - x0) < 15.0
        or (n - 1 - last) * step > 600.0
        or den <= 0.0
    ):
        if voc_high:
            return {
                "state": "high_voc",
                "rate_ppm_per_h": None,
                "level": voc_level,
                "eta_minutes": None,
                "target_ppm": None,
                "action": "ventilate_now",
                "voc_index": voc_val,
                "voc_level": voc_level,
            }
        return None

    rate = _js_round(((m * sxy - sx * sy) / den) * 60.0)
    cur = float(cur_co2) if _is_num(cur_co2) else y1
    t1, t2, t3 = co2_thr

    eta: int | None = None
    target: int | None = None
    action = "none"

    if rate >= 60:
        state = "rising"
        fast = rate >= 150
        tgt = t2 if cur < t2 else (t3 if cur < t3 else 0)
        raw_eta = _js_round(((tgt - cur) / rate) * 60.0) if tgt else 0
        far = cur < t2 and raw_eta > 180
        if cur >= t2:
            level = "bad" if fast else "poor"
        elif far:
            level = "moderate" if cur >= t1 else "good"
        else:
            level = "poor" if fast else "moderate"

        if cur >= t2 or (not far and raw_eta < 5):
            action = "ventilate_now"
        elif not far and cur < t2 and raw_eta >= 5:
            eta = raw_eta
            target = int(tgt)
    elif rate <= -80:
        state = "ventilating"
        level = "good"
        raw_eta = _js_round(((cur - t1) / -rate) * 60.0) if cur > t1 else 0
        if 5 <= raw_eta <= 180:
            eta = raw_eta
            target = int(t1)
    else:
        state = "steady"
        level = threshold_level(cur, co2_thr)
        if cur >= t2:
            action = "ventilate_now"

    if voc_high:
        level = max_level(level, voc_level)
        action = "keep_airing_out" if rate <= -80 else "ventilate_now"
    elif voc_level == "moderate":
        level = max_level(level, "moderate")

    return {
        "state": state,
        "rate_ppm_per_h": rate,
        "level": level,
        "eta_minutes": eta,
        "target_ppm": target,
        "action": action,
        "voc_index": voc_val,
        "voc_level": voc_level,
    }


def calc_comfort(
    temp_c: float | None,
    humidity_rh: float | None,
    dew_point_c: float | None = None,
) -> dict[str, Any] | None:
    """Assess thermal comfort and mold risk from temperature, RH, and dew point."""
    if not _is_num(temp_c) or not _is_num(humidity_rh):
        return None

    tc = float(temp_c)
    rh = float(humidity_rh)
    dp: float | None = float(dew_point_c) if _is_num(dew_point_c) else None
    if dp is None and rh > 0.0:
        alpha = math.log(rh / 100.0) + (17.62 * tc) / (243.12 + tc)
        dp = (243.12 * alpha) / (17.62 - alpha)

    dp_out = round(dp, 1) if dp is not None else None

    if rh > 65.0:
        state = "mold_risk"
        advice = "dehumidify"
        level = (
            "bad"
            if rh >= 75.0 or (rh >= 70.0 and dp is not None and dp >= 22.0)
            else "poor"
        )
    elif dp is not None and dp > 20.0:
        state = "muggy"
        advice = "dehumidify_or_cool"
        level = "poor" if dp >= 22.0 else "moderate"
    elif rh < 35.0:
        state = "dry_air"
        advice = "humidify_if_needed"
        level = "poor" if rh < 25.0 else "moderate"
    elif tc > 27.0:
        state = "warm"
        advice = "none"
        level = "moderate"
    elif tc < 18.0:
        state = "cool"
        advice = "none"
        level = "moderate"
    else:
        state = "comfortable"
        advice = "low_mold_risk"
        level = "good"

    return {
        "state": state,
        "level": level,
        "advice": advice,
        "temperature_c": round(tc, 1),
        "humidity_rh": round(rh, 1),
        "dew_point_c": dp_out,
    }


def calc_week(
    history_7d: dict[str, Any] | None,
    co2_thr: tuple[float, float, float] = DEFAULT_CO2_THRESHOLDS,
    voc_thr: tuple[float, float, float] = DEFAULT_VOC_THRESHOLDS,
) -> dict[str, Any] | None:
    """Compute 7-day CO2 + VOC summary statistics and daily breakdown."""
    if not isinstance(history_7d, dict):
        return None

    co2_arr = history_7d.get("co2_ppm")
    voc_arr = history_7d.get("voc_index")
    if not isinstance(co2_arr, list):
        co2_arr = []
    if not isinstance(voc_arr, list):
        voc_arr = []
    n = max(len(co2_arr), len(voc_arr))
    if not n:
        return None

    raw_step = history_7d.get("step_s")
    step = float(raw_step) if _is_num(raw_step) and raw_step > 0 else 1800.0
    raw_end = history_7d.get("end")
    end_ts = float(raw_end) if _is_num(raw_end) else None

    co2_t2 = float(co2_thr[1])
    voc_t2 = float(voc_thr[1])

    def _new_bucket(key: int, label: str) -> dict[str, Any]:
        return {
            "key": key,
            "label": label,
            "lo": float("inf"),
            "hi": 0.0,
            "sum": 0.0,
            "cnt": 0,
            "over": 0,
            "v_hi": 0.0,
            "v_cnt": 0,
            "v_over": 0,
        }

    def _add(bucket: dict[str, Any], co2_v: float | None, voc_v: float | None) -> None:
        if co2_v is not None:
            bucket["cnt"] += 1
            bucket["sum"] += co2_v
            if co2_v < bucket["lo"]:
                bucket["lo"] = co2_v
            if co2_v > bucket["hi"]:
                bucket["hi"] = co2_v
            if co2_v > co2_t2:
                bucket["over"] += 1
        if voc_v is not None:
            bucket["v_cnt"] += 1
            if voc_v > bucket["v_hi"]:
                bucket["v_hi"] = voc_v
            if voc_v > voc_t2:
                bucket["v_over"] += 1

    def _day_key(ts: float) -> tuple[int, str]:
        dt = datetime.fromtimestamp(ts, tz=timezone.utc).astimezone()
        return dt.year * 10000 + dt.month * 100 + dt.day, dt.strftime("%Y-%m-%d")

    tot = _new_bucket(0, "")
    buckets: dict[int, dict[str, Any]] = {}

    for i in range(n):
        c_raw = co2_arr[i] if i < len(co2_arr) else None
        v_raw = voc_arr[i] if i < len(voc_arr) else None
        c_val = float(c_raw) if _is_num(c_raw) else None
        v_val = float(v_raw) if _is_num(v_raw) else None
        if c_val is None and v_val is None:
            continue
        back = (n - 1 - i) * step
        if end_ts is not None:
            key, label = _day_key(end_ts - back)
        else:
            age_days = int(back // 86400)
            key = -age_days
            label = f"{age_days}-{age_days + 1}d ago" if age_days else "0-24h"
        bucket = buckets.get(key)
        if bucket is None:
            bucket = _new_bucket(key, label)
            buckets[key] = bucket
        _add(bucket, c_val, v_val)
        _add(tot, c_val, v_val)

    if tot["cnt"] < 2:
        return None

    def _hrs(samples: int) -> float:
        return round(samples * step / 3600.0, 1)

    has_voc = tot["v_cnt"] >= 2
    co2_avg = _js_round(tot["sum"] / tot["cnt"])
    voc_hrs = _hrs(tot["v_over"]) if has_voc else None
    voc_level = (
        ("poor" if voc_hrs >= 3.0 else "moderate" if voc_hrs >= 1.0 else "good")
        if has_voc and voc_hrs is not None
        else "good"
    )
    overall_level = max_level(threshold_level(co2_avg, co2_thr), voc_level)

    days: list[dict[str, Any]] = []
    for b in sorted(buckets.values(), key=lambda x: x["key"], reverse=True):
        entry: dict[str, Any] = {
            "date": b["label"],
            "co2_min_ppm": _js_round(b["lo"]) if b["cnt"] else None,
            "co2_avg_ppm": _js_round(b["sum"] / b["cnt"]) if b["cnt"] else None,
            "co2_max_ppm": _js_round(b["hi"]) if b["cnt"] else None,
            "co2_hours_over": _hrs(b["over"]) if b["cnt"] else None,
        }
        if has_voc:
            entry["voc_max"] = _js_round(b["v_hi"]) if b["v_cnt"] else None
            entry["voc_hours_over"] = _hrs(b["v_over"]) if b["v_cnt"] else None
        days.append(entry)

    return {
        "level": overall_level,
        "co2_min_ppm": _js_round(tot["lo"]),
        "co2_avg_ppm": co2_avg,
        "co2_max_ppm": _js_round(tot["hi"]),
        "co2_hours_over_threshold": _hrs(tot["over"]),
        "co2_threshold_ppm": int(co2_t2),
        "has_voc": has_voc,
        "voc_max": _js_round(tot["v_hi"]) if has_voc else None,
        "voc_hours_over_threshold": voc_hrs,
        "voc_threshold": int(voc_t2) if has_voc else None,
        "daily_breakdown": days,
    }


def compute_insights(
    status: dict[str, Any] | None,
    history_1h: dict[str, Any] | None,
    history_7d: dict[str, Any] | None,
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Compute all three Insights blocks from status, 1h/7d history, and optional config."""
    readings = status.get("readings") if isinstance(status, dict) else None
    if not isinstance(readings, dict):
        readings = {}
    co2_thr, voc_thr = extract_thresholds(config)
    return {
        "vent": calc_vent(
            history_1h,
            readings.get("co2_ppm"),
            co2_thr,
            readings.get("voc_index"),
            voc_thr,
        ),
        "comfort": calc_comfort(
            readings.get("temperature_c"),
            readings.get("humidity_rh"),
            readings.get("dew_point_c"),
        ),
        "week": calc_week(history_7d, co2_thr, voc_thr),
    }
