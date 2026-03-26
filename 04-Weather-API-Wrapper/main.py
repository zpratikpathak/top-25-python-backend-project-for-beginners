import hashlib
import os
import time
from collections import defaultdict
from typing import Any, Optional

import httpx
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException, Query

load_dotenv()

CACHE_TTL = 300
OWM_BASE = "https://api.openweathermap.org/data/2.5"


class TTLCache:
    def __init__(self) -> None:
        self._store: dict[str, tuple[float, Any]] = {}
        self.hits = 0
        self.misses = 0

    def get(self, key: str) -> Optional[Any]:
        now = time.time()
        entry = self._store.get(key)
        if entry is None:
            self.misses += 1
            return None
        expires_at, value = entry
        if now >= expires_at:
            del self._store[key]
            self.misses += 1
            return None
        self.hits += 1
        return value

    def set(self, key: str, value: Any) -> None:
        self._store[key] = (time.time() + CACHE_TTL, value)

    def clear(self) -> int:
        n = len(self._store)
        self._store.clear()
        return n


cache = TTLCache()
app = FastAPI(title="Weather API Wrapper", version="1.0.0")


def _api_key() -> Optional[str]:
    key = os.getenv("WEATHER_API_KEY")
    return key.strip() if key and key.strip() else None


def _city_seed(city: str) -> int:
    return int(hashlib.sha256(city.lower().encode()).hexdigest()[:8], 16)


def _mock_current(city: str) -> dict[str, Any]:
    s = _city_seed(city)
    temp = 8.0 + (s % 220) / 10.0
    humidity = 35 + (s >> 3) % 45
    wind = round(0.5 + (s % 80) / 10.0, 1)
    conditions = [
        "clear sky",
        "few clouds",
        "scattered clouds",
        "light rain",
        "overcast clouds",
        "mist",
    ]
    desc = conditions[s % len(conditions)]
    return {
        "city": city.strip().title(),
        "source": "mock",
        "temperature_c": round(temp, 1),
        "feels_like_c": round(temp - 1 + (s % 5), 1),
        "humidity_percent": humidity,
        "description": desc,
        "wind_speed_mps": wind,
        "wind_direction_deg": (s * 7) % 360,
        "pressure_hpa": 1000 + (s % 40),
        "visibility_m": 8000 + (s % 4000),
    }


def _mock_forecast(city: str, days: int) -> dict[str, Any]:
    s = _city_seed(city)
    daily: list[dict[str, Any]] = []
    for d in range(days):
        day_s = s + d * 9973
        hi = 10.0 + (day_s % 200) / 10.0
        lo = hi - 4 - (day_s % 5)
        daily.append(
            {
                "day_offset": d + 1,
                "temp_min_c": round(lo, 1),
                "temp_max_c": round(hi, 1),
                "humidity_percent": 40 + (day_s >> 2) % 40,
                "description": ["clear sky", "partly cloudy", "cloudy", "light rain"][
                    day_s % 4
                ],
                "wind_speed_mps": round(1 + (day_s % 60) / 10.0, 1),
                "precipitation_probability_percent": (day_s * 3) % 100,
            }
        )
    return {"city": city.strip().title(), "source": "mock", "days": days, "forecast": daily}


async def _owm_current(client: httpx.AsyncClient, city: str, api_key: str) -> dict[str, Any]:
    r = await client.get(
        f"{OWM_BASE}/weather",
        params={"q": city, "appid": api_key, "units": "metric"},
        timeout=15.0,
    )
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="City not found")
    r.raise_for_status()
    data = r.json()
    w = data.get("weather", [{}])[0]
    main = data.get("main", {})
    wind = data.get("wind", {})
    return {
        "city": data.get("name", city),
        "source": "openweathermap",
        "temperature_c": main.get("temp"),
        "feels_like_c": main.get("feels_like"),
        "humidity_percent": main.get("humidity"),
        "description": w.get("description", "").lower() or "unknown",
        "wind_speed_mps": wind.get("speed"),
        "wind_direction_deg": wind.get("deg"),
        "pressure_hpa": main.get("pressure"),
        "visibility_m": data.get("visibility"),
    }


async def _owm_forecast(
    client: httpx.AsyncClient, city: str, days: int, api_key: str
) -> dict[str, Any]:
    r = await client.get(
        f"{OWM_BASE}/forecast",
        params={"q": city, "appid": api_key, "units": "metric"},
        timeout=15.0,
    )
    if r.status_code == 404:
        raise HTTPException(status_code=404, detail="City not found")
    r.raise_for_status()
    data = r.json()
    name = data.get("city", {}).get("name", city)
    items = data.get("list", [])
    by_date: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for it in items:
        dt_txt = it.get("dt_txt", "")
        date_key = dt_txt[:10] if len(dt_txt) >= 10 else dt_txt
        if date_key:
            by_date[date_key].append(it)
    sorted_dates = sorted(by_date.keys())[:days]
    forecast: list[dict[str, Any]] = []
    for i, dk in enumerate(sorted_dates):
        slots = by_date[dk]
        temps = [s.get("main", {}).get("temp") for s in slots if s.get("main", {}).get("temp") is not None]
        hums = [
            s.get("main", {}).get("humidity")
            for s in slots
            if s.get("main", {}).get("humidity") is not None
        ]
        winds = [
            s.get("wind", {}).get("speed")
            for s in slots
            if s.get("wind", {}).get("speed") is not None
        ]
        pops = [s.get("pop") for s in slots if s.get("pop") is not None]
        mid = slots[len(slots) // 2]
        w = mid.get("weather", [{}])[0]
        forecast.append(
            {
                "date": dk,
                "day_offset": i + 1,
                "temp_min_c": round(min(temps), 1) if temps else None,
                "temp_max_c": round(max(temps), 1) if temps else None,
                "humidity_percent": round(sum(hums) / len(hums)) if hums else None,
                "description": (w.get("description") or "").lower() or "unknown",
                "wind_speed_mps": round(sum(winds) / len(winds), 1) if winds else None,
                "precipitation_probability_percent": round(max(pops) * 100) if pops else None,
            }
        )
    return {
        "city": name,
        "source": "openweathermap",
        "days": len(forecast),
        "forecast": forecast,
    }


@app.get("/api/weather")
async def get_weather(city: str = Query(..., min_length=1, description="City name")):
    key = f"weather:{city.strip().lower()}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    api_key = _api_key()
    if not api_key:
        payload = _mock_current(city)
    else:
        async with httpx.AsyncClient() as client:
            payload = await _owm_current(client, city, api_key)

    cache.set(key, payload)
    return payload


@app.get("/api/weather/forecast")
async def get_forecast(
    city: str = Query(..., min_length=1),
    days: int = Query(5, ge=1, le=5),
):
    key = f"forecast:{city.strip().lower()}:{days}"
    cached = cache.get(key)
    if cached is not None:
        return cached

    api_key = _api_key()
    if not api_key:
        payload = _mock_forecast(city, days)
    else:
        async with httpx.AsyncClient() as client:
            payload = await _owm_forecast(client, city, days, api_key)

    cache.set(key, payload)
    return payload


@app.get("/api/cache/stats")
def cache_stats():
    total = cache.hits + cache.misses
    hit_rate = (cache.hits / total * 100) if total else 0.0
    return {
        "hits": cache.hits,
        "misses": cache.misses,
        "total_lookups": total,
        "hit_rate_percent": round(hit_rate, 2),
        "ttl_seconds": CACHE_TTL,
    }


@app.delete("/api/cache")
def clear_cache():
    n = cache.clear()
    return {"cleared_entries": n, "message": "Cache cleared"}


@app.get("/")
def root():
    return {"service": "weather-api-wrapper", "docs": "/docs"}
