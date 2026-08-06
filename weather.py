#!/usr/bin/env python3
"""Fetch a two-day forecast for Tutaev and prepare a VK draft."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen
from zoneinfo import ZoneInfo

MOSCOW = ZoneInfo("Europe/Moscow")
LATITUDE = 57.8846
LONGITUDE = 39.5406
API_URL = "https://api.met.no/weatherapi/locationforecast/2.0/compact"
SOURCE_URL = "https://www.met.no/en"
USER_AGENT = "vk-news-collector/1.0 github.com/lianasemenova76-star/vk-news-collector"

SYMBOLS = {
    "clearsky": "ясно", "fair": "малооблачно", "partlycloudy": "переменная облачность",
    "cloudy": "облачно", "fog": "туман", "rain": "дождь", "lightrain": "небольшой дождь",
    "heavyrain": "сильный дождь", "rainshowers": "ливни", "lightrainshowers": "небольшие ливни",
    "heavyrainshowers": "сильные ливни", "sleet": "дождь со снегом", "snow": "снег",
    "lightsnow": "небольшой снег", "heavysnow": "сильный снег", "snowshowers": "снегопад",
}


def fetch_forecast() -> dict:
    query = urlencode({"lat": f"{LATITUDE:.4f}", "lon": f"{LONGITUDE:.4f}"})
    request = Request(f"{API_URL}?{query}", headers={"User-Agent": USER_AGENT})
    try:
        with urlopen(request, timeout=60) as response:
            return json.load(response)
    except (HTTPError, URLError, TimeoutError, json.JSONDecodeError) as exc:
        raise RuntimeError(f"Weather request failed: {exc}") from exc


def symbol_text(code: str) -> str:
    base = code.removesuffix("_day").removesuffix("_night").removesuffix("_polartwilight")
    if "thunder" in base:
        return "дождь и гроза"
    return SYMBOLS.get(base, "переменная облачность")


def daily_forecasts(document: dict, days: int = 2) -> list[dict]:
    buckets: dict[str, list[dict]] = {}
    for item in document.get("properties", {}).get("timeseries", []):
        moment = datetime.fromisoformat(item["time"].replace("Z", "+00:00")).astimezone(MOSCOW)
        buckets.setdefault(moment.date().isoformat(), []).append({"moment": moment, "data": item["data"]})
    today = datetime.now(MOSCOW).date().isoformat()
    result = []
    for day, items in sorted(buckets.items()):
        if day < today:
            continue
        temperatures = [float(item["data"]["instant"]["details"]["air_temperature"]) for item in items]
        winds = [float(item["data"]["instant"]["details"].get("wind_speed", 0)) for item in items]
        codes = []
        precipitation = 0.0
        for item in items:
            data = item["data"]
            for interval in ("next_1_hours", "next_6_hours", "next_12_hours"):
                if interval in data:
                    summary = data[interval].get("summary", {})
                    if summary.get("symbol_code"):
                        codes.append(symbol_text(summary["symbol_code"]))
                    precipitation += float(data[interval].get("details", {}).get("precipitation_amount", 0))
                    break
        condition = Counter(codes).most_common(1)[0][0] if codes else "без существенных осадков"
        result.append({
            "date": day,
            "min_temperature": round(min(temperatures)),
            "max_temperature": round(max(temperatures)),
            "condition": condition,
            "max_wind_speed": round(max(winds), 1),
            "precipitation_mm": round(precipitation, 1),
        })
        if len(result) == days:
            break
    if len(result) < days:
        raise RuntimeError("Weather API did not return two complete forecast days")
    return result


def signed(value: int) -> str:
    return f"{value:+d}"


def render_vk(days: list[dict]) -> str:
    labels = ["Сегодня", "Завтра"]
    lines = ["ПОГОДА В ТУТАЕВЕ НА СЕГОДНЯ И ЗАВТРА", ""]
    for label, item in zip(labels, days):
        lines.append(
            f"🔵 {label}: ночью до {signed(item['min_temperature'])} °C, "
            f"днём до {signed(item['max_temperature'])} °C, {item['condition']}. "
            f"Ветер до {item['max_wind_speed']:g} м/с."
        )
    lines.extend(["", "Данные: MET Norway."])
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--output-dir", type=Path, default=Path("weather-report"))
    parser.add_argument("--input", type=Path, help="Read saved API JSON instead of requesting the network")
    args = parser.parse_args()
    try:
        document = json.loads(args.input.read_text(encoding="utf-8")) if args.input else fetch_forecast()
        days = daily_forecasts(document)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    generated_at = datetime.now(MOSCOW).isoformat(timespec="seconds")
    result = {
        "generated_at": generated_at,
        "location": "Тутаев",
        "coordinates": {"latitude": LATITUDE, "longitude": LONGITUDE},
        "source": {"name": "MET Norway", "url": SOURCE_URL},
        "days": days,
        "vk_draft": render_vk(days),
        "published": False,
    }
    args.output_dir.mkdir(parents=True, exist_ok=True)
    (args.output_dir / "latest.json").write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    (args.output_dir / "vk-draft.txt").write_text(result["vk_draft"] + "\n", encoding="utf-8")
    print(result["vk_draft"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
