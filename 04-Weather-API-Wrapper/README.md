# Weather API Wrapper

A small FastAPI service that exposes current weather and a short forecast. It can call the OpenWeatherMap API when you provide a key, or run in **mock mode** with no external accounts so you can try the API immediately.

## Features

- **Current weather** — temperature, humidity, description, wind, pressure, visibility, and related fields.
- **Forecast** — up to five days (grouped by day when using OpenWeatherMap’s 5-day / 3-hour forecast).
- **In-memory cache** — responses are cached for **5 minutes** to limit duplicate upstream calls.
- **Cache observability** — hit/miss counters and hit rate at `GET /api/cache/stats`.
- **Cache reset** — `DELETE /api/cache` drops all cached entries.
- **Mock mode** — if `WEATHER_API_KEY` is unset or empty, responses use deterministic fake data derived from the city name (same city → stable values until the cache expires).

## Tech stack

- Python 3.10+
- [FastAPI](https://fastapi.tiangolo.com/)
- [Uvicorn](https://www.uvicorn.org/)
- [httpx](https://www.python-httpx.org/) (async HTTP client)
- [python-dotenv](https://pypi.org/project/python-dotenv/) (optional `.env` loading)

## Installation

```bash
cd 04-Weather-API-Wrapper
python -m venv .venv
```

Activate the virtual environment (Windows PowerShell):

```powershell
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```bash
pip install -r requirements.txt
```

Copy the environment template and optionally add your key:

```bash
copy .env.example .env
```

Run the app:

```bash
uvicorn main:app --reload
```

Open interactive docs at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

## OpenWeatherMap API key (optional)

1. Create a free account at [https://openweathermap.org/api](https://openweathermap.org/api).
2. Subscribe to the **Current Weather** and **5 Day / 3 Hour Forecast** products (free tier is enough for this project).
3. Copy your API key from the dashboard.
4. Set `WEATHER_API_KEY` in `.env` or in your shell.

If the variable is missing or blank, the app stays in **mock mode** and does not call OpenWeatherMap.

## API endpoints

Base URL in local development: `http://127.0.0.1:8000`

### `GET /api/weather?city={city}`

Current weather for a city.

```bash
curl "http://127.0.0.1:8000/api/weather?city=London"
```

### `GET /api/weather/forecast?city={city}&days={1-5}`

Forecast; `days` defaults to `5` and must be between 1 and 5.

```bash
curl "http://127.0.0.1:8000/api/weather/forecast?city=Paris&days=3"
```

### `GET /api/cache/stats`

Cache hit/miss statistics and configured TTL.

```bash
curl "http://127.0.0.1:8000/api/cache/stats"
```

### `DELETE /api/cache`

Clear all cached entries.

```bash
curl -X DELETE "http://127.0.0.1:8000/api/cache"
```

## Project structure

```text
04-Weather-API-Wrapper/
├── main.py           # FastAPI app, cache, mock + OpenWeatherMap integration
├── requirements.txt  # Python dependencies
├── .env.example      # Environment variable template
└── README.md         # This file
```
