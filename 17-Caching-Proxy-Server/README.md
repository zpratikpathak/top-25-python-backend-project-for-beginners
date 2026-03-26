# Caching Proxy Server

A small **FastAPI** service that forwards HTTP GET requests to upstream URLs, keeps responses in an **in-memory cache**, and exposes endpoints to inspect and clear the cache.

## Features

- **Forward proxy**: `GET /api/proxy?url=...` fetches an upstream resource, returns it, and caches the result.
- **TTL**: Default cache lifetime is **5 minutes**; override with `?ttl=` (seconds, 1–86400).
- **LRU eviction**: At most **100** entries; when full, the least recently used entry is removed before inserting a new one.
- **Per-entry metrics**: Each cache entry tracks how many times it was served from cache (`hit_count`).
- **Global stats**: Aggregate hits, misses, hit rate, entry count, and total cached body size.
- **Admin APIs**: List entries, full clear, or invalidate by URL.
- **Resilience**: Validates URLs, uses async `httpx` with timeout, and maps timeouts / connection failures to HTTP errors.

## Caching strategy

1. **Key**: The request `url` query string (trimmed). Only `http` and `https` are allowed; the URL must include scheme and host.
2. **Lookup**: On each proxy request, if a non-expired entry exists for that URL, it is returned immediately (**hit**). The entry is moved to the **most recently used** side of the LRU structure.
3. **Miss**: If there is no entry or it is expired, the server fetches upstream (**miss**), stores body, status code, content type, and safe response headers, then returns the response.
4. **TTL**: Expiry is `cached_at + ttl` where `ttl` comes from that proxy request (default 300s). Re-fetching the same URL replaces the stored entry with a new TTL window.
5. **Eviction**: If the cache already has 100 entries and a new one is added, the **least recently used** entry (among current keys) is dropped first.

## Tech stack

- Python 3.10+
- [FastAPI](https://fastapi.tiangolo.com/)
- [Uvicorn](https://www.uvicorn.org/) (ASGI server)
- [HTTPX](https://www.python-httpx.org/) (async HTTP client)

## Installation

```bash
cd 17-Caching-Proxy-Server
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Or:

```bash
python main.py
```

Interactive docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

## API

### `GET /api/proxy`

Fetch `url` through the proxy; use cache when valid.

```bash
curl -s "http://127.0.0.1:8000/api/proxy?url=https://httpbin.org/get"
```

Custom TTL (60 seconds):

```bash
curl -s "http://127.0.0.1:8000/api/proxy?url=https://httpbin.org/uuid&ttl=60"
```

### `GET /api/cache/stats`

```bash
curl -s "http://127.0.0.1:8000/api/cache/stats"
```

Example shape: `total_entries`, `hits`, `misses`, `hit_rate`, `total_size_bytes`.

### `GET /api/cache/entries`

```bash
curl -s "http://127.0.0.1:8000/api/cache/entries"
```

Each item includes `url`, `cached_at`, `expires_at`, `size`, `hit_count`, and `expired`.

### `DELETE /api/cache`

Clear entire cache:

```bash
curl -s -X DELETE "http://127.0.0.1:8000/api/cache"
```

Invalidate one URL:

```bash
curl -s -X DELETE "http://127.0.0.1:8000/api/cache?url=https://httpbin.org/get"
```

## Project structure

```
17-Caching-Proxy-Server/
├── main.py           # FastAPI app, cache, proxy logic
├── requirements.txt  # Dependencies
└── README.md         # This file
```

## Error behavior

| Situation              | Typical response |
|------------------------|------------------|
| Invalid / disallowed URL | `400` |
| Upstream timeout       | `504` |
| Upstream connection error | `502` |

## License

Use freely for learning and demos.
