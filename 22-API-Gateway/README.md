# FastAPI API Gateway

A small API gateway built with FastAPI. It registers upstream services in SQLite, proxies arbitrary HTTP methods to them under `/gateway/{service}/...`, and adds API-key auth on gateway routes, per-client rate limits, an in-memory circuit breaker, structured request logging, and periodic health checks.

## Features

- **Reverse proxy**: Forwards method, query string, body, and most headers to the registered `base_url` using `httpx`.
- **Service registry**: CRUD-style registration stored in SQLite (name, base URL, health path, optional per-service rate limit, circuit settings).
- **Gateway authentication**: All `/gateway/*` traffic requires a valid `X-API-Key` issued by the gateway.
- **Rate limiting**: Sliding one-minute window per `(API key or client IP, service)` in memory; default 100 RPM, overridable per service.
- **Circuit breaker**: Per-service state in memory—`closed` → `open` after repeated upstream failures → after a timeout, `half-open` allows a single probe; success closes, failure reopens.
- **Observability**: Request logs and aggregate stats (counts, error rate, average latency) from SQLite; background health polling updates registry health flags.

## Architecture (text diagram)

```
Clients
   |
   |  X-API-Key on /gateway/*
   v
+------------------+
|   FastAPI app    |
|  - auth middleware
|  - rate limit    |
|  - circuit brk   |
+------------------+
   |                    \
   | SQLAlchemy          \ httpx
   v                      v
+------------+      +--------------+
|  SQLite    |      | Upstream     |
| services   |      | microservices|
| api_keys   |      +--------------+
| request_logs|
+------------+

In-memory only: circuit state, rate-limit timestamps.
```

## Circuit breaker behavior

1. **Closed**: Normal operation. Upstream **5xx** responses and **connection errors** increment a failure counter. When the counter reaches the service’s **failure threshold** (default 5), the breaker moves to **open**.
2. **Open**: Gateway returns **503** for that service until **recovery timeout** (default 60s) elapses, then it moves to **half-open**.
3. **Half-open**: Only **one** concurrent probe is allowed. If the probe gets a response with status below **500**, the breaker returns to **closed** and failures reset. If the probe fails (5xx or network error), the breaker goes **open** again.

**Note:** **4xx** responses from upstream are treated as success for the circuit (the service is considered reachable).

## Tech stack

| Layer        | Choice                          |
|-------------|----------------------------------|
| Framework   | FastAPI                          |
| Server      | Uvicorn                          |
| HTTP client | httpx (async)                    |
| Persistence | SQLAlchemy 2 + SQLite (aiosqlite)|

## Installation

```bash
cd 22-API-Gateway
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

The app listens on **port 8000** by default and creates `gateway.db` in the working directory on first startup.

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/auth/token` | Issue a new gateway API key |
| POST | `/api/services` | Register a service |
| GET | `/api/services` | List services and last known health |
| DELETE | `/api/services/{name}` | Remove a service |
| GET | `/api/services/{name}/health` | Live probe of upstream health URL |
| GET | `/api/gateway/stats` | Aggregated stats from logs |
| GET | `/api/gateway/logs` | Paginated recent logs (`offset`, `limit`) |
| * | `/gateway/{service_name}/{path}` | Proxy to registered service (requires `X-API-Key`) |

### Register service body (JSON)

- `name` (string, required)
- `base_url` (string, required)
- `health_check_path` (string, default `"/health"`)
- `rate_limit_per_minute` (integer, optional; omit for default 100)
- `circuit_failure_threshold` (integer, default `5`)
- `circuit_recovery_seconds` (integer, default `60`)

## Example workflow (curl)

Replace host/port if needed. The examples use [httpbin.org](https://httpbin.org) as a stand-in upstream.

**1. Issue an API key**

```bash
curl -s -X POST http://127.0.0.1:8000/api/auth/token
```

Save the `token` value as `GATEWAY_KEY`.

**2. Register an upstream**

```bash
curl -s -X POST http://127.0.0.1:8000/api/services ^
  -H "Content-Type: application/json" ^
  -d "{\"name\":\"httpbin\",\"base_url\":\"https://httpbin.org\",\"health_check_path\":\"/get\",\"rate_limit_per_minute\":60}"
```

(On PowerShell you can use `Invoke-RestMethod` with a hashtable instead of escaping JSON.)

**3. List services**

```bash
curl -s http://127.0.0.1:8000/api/services
```

**4. Call the gateway (proxied GET)**

```bash
curl -s -H "X-API-Key: YOUR_TOKEN_HERE" http://127.0.0.1:8000/gateway/httpbin/get
```

**5. Proxied POST with JSON body**

```bash
curl -s -X POST http://127.0.0.1:8000/gateway/httpbin/post ^
  -H "X-API-Key: YOUR_TOKEN_HERE" ^
  -H "Content-Type: application/json" ^
  -d "{\"hello\":\"world\"}"
```

**6. Stats and logs**

```bash
curl -s http://127.0.0.1:8000/api/gateway/stats
curl -s "http://127.0.0.1:8000/api/gateway/logs?offset=0&limit=20"
```

**7. Live health check for one service**

```bash
curl -s http://127.0.0.1:8000/api/services/httpbin/health
```

**8. Unregister**

```bash
curl -s -X DELETE http://127.0.0.1:8000/api/services/httpbin
```

**9. Missing or bad key on gateway (401)**

```bash
curl -i http://127.0.0.1:8000/gateway/httpbin/get
```

## Project structure

```
22-API-Gateway/
├── main.py           # Application, models, gateway logic
├── requirements.txt  # Python dependencies
├── README.md         # This file
└── gateway.db        # Created at runtime (SQLite)
```

## License

Use and modify freely for learning or production as you see fit.
