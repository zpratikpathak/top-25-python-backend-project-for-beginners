# Rate Limiter API

A small FastAPI service that demonstrates two classic rate limiting strategies as HTTP middleware. Clients are identified by IP (`X-Forwarded-For` first hop, otherwise the connection host). Limits are stored in memory and reset when the process restarts.

## Features

- **Token bucket** and **sliding window** limiters on separate demo routes
- **RFC-style headers**: `X-RateLimit-Limit`, `X-RateLimit-Remaining`, `X-RateLimit-Reset` (Unix timestamp)
- **429 Too Many Requests** when a limit is exceeded (with JSON `detail`)
- **Status**, **configuration**, and **stats** endpoints for demos and inspection
- Default limit: **10 requests per 60 seconds** per client per algorithm (independent counters per route)

## Algorithms

### Token bucket

Each client has a bucket holding up to `max_requests` tokens. Tokens refill continuously at `max_requests / window_seconds` per second. Each allowed request spends one token. Bursts are allowed up to the bucket size; sustained traffic is smoothed by the refill rate.

### Sliding window

Each client keeps timestamps of recent requests. Entries older than `window_seconds` are dropped. A new request is allowed only if fewer than `max_requests` timestamps remain in the window. This enforces a hard cap on how many calls can occur in any rolling `window_seconds` interval (stricter than token bucket for burst + average).

## Tech stack

- Python 3
- [FastAPI](https://fastapi.tiangolo.com/)
- [Uvicorn](https://www.uvicorn.org/)
- [Pydantic](https://docs.pydantic.dev/) (via FastAPI)
- [Starlette](https://www.starlette.io/) middleware

## Installation

```bash
cd 18-Rate-Limiter-API
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Open interactive docs at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

## API overview

| Method | Path | Description |
|--------|------|-------------|
| GET | `/api/limited/token-bucket` | Token bucket (10/min default) |
| GET | `/api/limited/sliding-window` | Sliding window (10/min default) |
| GET | `/api/unlimited` | No rate limit |
| GET | `/api/rate-limit/status?client_id={id}` | Remaining / reset for both algorithms for that client id (use IP string) |
| POST | `/api/rate-limit/configure` | Set `algorithm`, `max_requests`, `window_seconds` |
| GET | `/api/rate-limit/stats` | Totals and per-client request / blocked counts |

## curl examples

Replace `127.0.0.1` if needed. Use `-i` to see rate limit headers.

### Token bucket: burst until limited

```bash
curl -s -i "http://127.0.0.1:8000/api/limited/token-bucket"
```

Repeat quickly; after 10 successes within the refill window you should see `429` and the same header names on the error response.

```bash
for /L %i in (1,1,12) do @curl -s -o NUL -w "%%{http_code} " "http://127.0.0.1:8000/api/limited/token-bucket"
```

(PowerShell loop alternative:)

```powershell
1..12 | ForEach-Object { (Invoke-WebRequest "http://127.0.0.1:8000/api/limited/token-bucket" -SkipHttpErrorCheck).StatusCode }
```

### Sliding window: strict rolling cap

```bash
curl -s -i "http://127.0.0.1:8000/api/limited/sliding-window"
```

### Unlimited (no limit headers from middleware)

```bash
curl -s "http://127.0.0.1:8000/api/unlimited"
```

### Status for a client (pass the IP you want to inspect)

```bash
curl -s "http://127.0.0.1:8000/api/rate-limit/status?client_id=127.0.0.1"
```

### Simulate another client via `X-Forwarded-For`

```bash
curl -s -i -H "X-Forwarded-For: 203.0.113.50" "http://127.0.0.1:8000/api/limited/token-bucket"
```

### Configure limits (demo)

```bash
curl -s -X POST "http://127.0.0.1:8000/api/rate-limit/configure" ^
  -H "Content-Type: application/json" ^
  -d "{\"algorithm\": \"both\", \"max_requests\": 5, \"window_seconds\": 30}"
```

`algorithm` must be `token_bucket`, `sliding_window`, or `both`. Reconfiguration clears in-memory bucket/window state for that algorithm.

### Stats

```bash
curl -s "http://127.0.0.1:8000/api/rate-limit/stats"
```

## Project structure

```
18-Rate-Limiter-API/
├── main.py           # FastAPI app, limiter stores, middleware, routes
├── requirements.txt  # fastapi, uvicorn
└── README.md
```

## Notes

- Storage is **in-memory** only; scaling across processes requires a shared store (e.g. Redis).
- Token bucket and sliding window limits are **independent**; hitting one route does not decrement the other’s budget.
- Stats count requests to any path under `/api/` and count blocks when a limited route returns 429.
