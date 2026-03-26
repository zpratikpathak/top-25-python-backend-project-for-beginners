from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Optional
from urllib.parse import urlparse

import httpx
from fastapi import FastAPI, HTTPException, Query, Response
from fastapi.responses import JSONResponse

DEFAULT_TTL_SECONDS = 300
MAX_ENTRIES = 100
UPSTREAM_TIMEOUT = 30.0

HOP_BY_HOP = frozenset(
    {
        "connection",
        "keep-alive",
        "proxy-authenticate",
        "proxy-authorization",
        "te",
        "trailers",
        "transfer-encoding",
        "upgrade",
    }
)


@dataclass
class CacheEntry:
    body: bytes
    status_code: int
    content_type: Optional[str]
    cached_at: datetime
    expires_at: datetime
    hit_count: int = 0
    extra_headers: dict[str, str] = field(default_factory=dict)

    def size_bytes(self) -> int:
        return len(self.body)


class ProxyCache:
    def __init__(self, max_entries: int = MAX_ENTRIES) -> None:
        self._max = max_entries
        self._data: OrderedDict[str, CacheEntry] = OrderedDict()
        self.hits = 0
        self.misses = 0

    def _evict_if_needed(self) -> None:
        while len(self._data) >= self._max:
            self._data.popitem(last=False)

    def get(self, key: str) -> Optional[CacheEntry]:
        if key not in self._data:
            self.misses += 1
            return None
        entry = self._data[key]
        now = datetime.now(timezone.utc)
        if entry.expires_at <= now:
            del self._data[key]
            self.misses += 1
            return None
        self._data.move_to_end(key)
        entry.hit_count += 1
        self.hits += 1
        return entry

    def set(self, key: str, entry: CacheEntry) -> None:
        if key in self._data:
            del self._data[key]
        self._evict_if_needed()
        self._data[key] = entry
        self._data.move_to_end(key)

    def delete(self, key: str) -> bool:
        if key in self._data:
            del self._data[key]
            return True
        return False

    def clear(self) -> None:
        self._data.clear()

    def entries(self) -> list[tuple[str, CacheEntry]]:
        return list(self._data.items())

    def total_size_bytes(self) -> int:
        return sum(e.size_bytes() for e in self._data.values())

    def __len__(self) -> int:
        return len(self._data)


def normalize_cache_key(url: str) -> str:
    parsed = urlparse(url.strip())
    if not parsed.scheme or not parsed.netloc:
        raise ValueError("URL must include scheme and host")
    if parsed.scheme not in ("http", "https"):
        raise ValueError("Only http and https URLs are allowed")
    return url.strip()


def filter_response_headers(headers: httpx.Headers) -> dict[str, str]:
    out: dict[str, str] = {}
    for name, value in headers.items():
        lower = name.lower()
        if lower in HOP_BY_HOP or lower == "content-encoding":
            continue
        if lower in ("content-length", "content-type"):
            continue
        out[name] = value
    return out


app = FastAPI(title="Caching Proxy Server", version="1.0.0")
cache = ProxyCache(MAX_ENTRIES)


@app.get("/api/proxy")
async def proxy(
    url: str = Query(..., description="Upstream URL to fetch"),
    ttl: int = Query(DEFAULT_TTL_SECONDS, ge=1, le=86400, description="Cache TTL in seconds"),
) -> Response:
    try:
        key = normalize_cache_key(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e

    entry = cache.get(key)
    if entry is not None:
        headers = {**entry.extra_headers}
        if entry.content_type:
            headers["Content-Type"] = entry.content_type
        return Response(
            content=entry.body,
            status_code=entry.status_code,
            headers=headers,
        )

    async with httpx.AsyncClient(
        timeout=UPSTREAM_TIMEOUT,
        follow_redirects=True,
    ) as client:
        try:
            upstream = await client.get(key)
        except httpx.TimeoutException as e:
            raise HTTPException(status_code=504, detail="Upstream request timed out") from e
        except httpx.RequestError as e:
            raise HTTPException(
                status_code=502,
                detail=f"Upstream connection error: {e!s}",
            ) from e

    body = upstream.content
    ct = upstream.headers.get("content-type")
    now = datetime.now(timezone.utc)
    expires = datetime.fromtimestamp(now.timestamp() + ttl, tz=timezone.utc)
    extra = filter_response_headers(upstream.headers)

    cache.set(
        key,
        CacheEntry(
            body=body,
            status_code=upstream.status_code,
            content_type=ct,
            cached_at=now,
            expires_at=expires,
            hit_count=0,
            extra_headers=extra,
        ),
    )

    out_headers = dict(extra)
    if ct:
        out_headers["Content-Type"] = ct
    return Response(content=body, status_code=upstream.status_code, headers=out_headers)


@app.get("/api/cache/stats")
async def cache_stats() -> dict[str, Any]:
    total = cache.hits + cache.misses
    hit_rate = (cache.hits / total) if total else 0.0
    return {
        "total_entries": len(cache),
        "hits": cache.hits,
        "misses": cache.misses,
        "hit_rate": round(hit_rate, 4),
        "total_size_bytes": cache.total_size_bytes(),
    }


@app.get("/api/cache/entries")
async def cache_entries() -> JSONResponse:
    now = datetime.now(timezone.utc)
    items: list[dict[str, Any]] = []
    for url_key, e in cache.entries():
        items.append(
            {
                "url": url_key,
                "cached_at": e.cached_at.isoformat(),
                "expires_at": e.expires_at.isoformat(),
                "size": e.size_bytes(),
                "hit_count": e.hit_count,
                "expired": e.expires_at <= now,
            }
        )
    return JSONResponse(content={"entries": items})


@app.delete("/api/cache")
async def cache_invalidate(url: Optional[str] = Query(None)) -> dict[str, Any]:
    if url is None:
        n = len(cache)
        cache.clear()
        return {"cleared": True, "removed_count": n}
    try:
        key = normalize_cache_key(url)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e)) from e
    removed = cache.delete(key)
    return {"cleared": False, "url": key, "removed": removed}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000)
