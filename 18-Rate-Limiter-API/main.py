import threading
import time
from collections import defaultdict, deque
from typing import Any

from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from starlette.middleware.base import BaseHTTPMiddleware

TOKEN_BUCKET_PATH = "/api/limited/token-bucket"
SLIDING_WINDOW_PATH = "/api/limited/sliding-window"


class RateLimitConfig(BaseModel):
    algorithm: str = Field(..., pattern="^(token_bucket|sliding_window|both)$")
    max_requests: int = Field(..., ge=1, le=10_000)
    window_seconds: int = Field(..., ge=1, le=86400)


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip() or "unknown"
    if request.client:
        return request.client.host
    return "unknown"


class InMemoryStats:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self.total_requests = 0
        self.blocked_requests = 0
        self.per_client: dict[str, dict[str, int]] = defaultdict(
            lambda: {"requests": 0, "blocked": 0}
        )

    def record_request(self, ip: str) -> None:
        with self._lock:
            self.total_requests += 1
            self.per_client[ip]["requests"] += 1

    def record_blocked(self, ip: str) -> None:
        with self._lock:
            self.blocked_requests += 1
            self.per_client[ip]["blocked"] += 1

    def snapshot(self) -> dict[str, Any]:
        with self._lock:
            return {
                "total_requests": self.total_requests,
                "blocked_requests": self.blocked_requests,
                "per_client": {k: dict(v) for k, v in self.per_client.items()},
            }


class TokenBucketStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._buckets: dict[str, dict[str, float]] = {}
        self._capacity = 10
        self._window_seconds = 60.0

    @property
    def limit(self) -> int:
        return self._capacity

    def configure(self, capacity: int, window_seconds: float) -> None:
        with self._lock:
            self._capacity = capacity
            self._window_seconds = window_seconds
            self._buckets.clear()

    def try_consume(self, key: str) -> tuple[bool, int, int]:
        now = time.time()
        with self._lock:
            capacity = self._capacity
            window_seconds = self._window_seconds
            refill_rate = capacity / window_seconds if window_seconds > 0 else 0.0
            state = self._buckets.get(key)
            if state is None:
                state = {"tokens": float(capacity), "last": now}
                self._buckets[key] = state
            elapsed = now - state["last"]
            tokens = min(float(capacity), state["tokens"] + elapsed * refill_rate)
            state["last"] = now
            if tokens >= 1.0:
                tokens -= 1.0
                state["tokens"] = tokens
                remaining = int(tokens)
                reset_ts = int(now + (capacity - tokens) / refill_rate) if refill_rate > 0 else int(now + window_seconds)
                return True, remaining, reset_ts
            state["tokens"] = tokens
            need = 1.0 - tokens
            reset_ts = int(now + need / refill_rate) if refill_rate > 0 else int(now + window_seconds)
            return False, 0, reset_ts

    def peek(self, key: str) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            capacity = self._capacity
            window_seconds = self._window_seconds
            refill_rate = capacity / window_seconds if window_seconds > 0 else 0.0
            state = self._buckets.get(key)
            if state is None:
                return {
                    "limit": capacity,
                    "remaining": capacity,
                    "reset": int(now + window_seconds),
                }
            elapsed = now - state["last"]
            tokens = min(float(capacity), state["tokens"] + elapsed * refill_rate)
            remaining = int(tokens)
            reset_ts = int(now + (capacity - tokens) / refill_rate) if refill_rate > 0 else int(now + window_seconds)
            return {"limit": capacity, "remaining": remaining, "reset": reset_ts}


class SlidingWindowStore:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: dict[str, deque[float]] = {}
        self._max_requests = 10
        self._window_seconds = 60.0

    @property
    def limit(self) -> int:
        return self._max_requests

    def configure(self, max_requests: int, window_seconds: float) -> None:
        with self._lock:
            self._max_requests = max_requests
            self._window_seconds = window_seconds
            self._windows.clear()

    def try_consume(self, key: str) -> tuple[bool, int, int]:
        now = time.time()
        with self._lock:
            max_req = self._max_requests
            window = self._window_seconds
            dq = self._windows.get(key)
            if dq is None:
                dq = deque()
                self._windows[key] = dq
            cutoff = now - window
            while dq and dq[0] < cutoff:
                dq.popleft()
            if len(dq) < max_req:
                dq.append(now)
                remaining = max_req - len(dq)
                reset_ts = int(dq[0] + window) if dq else int(now + window)
                return True, remaining, reset_ts
            reset_ts = int(dq[0] + window)
            return False, 0, reset_ts

    def peek(self, key: str) -> dict[str, Any]:
        now = time.time()
        with self._lock:
            max_req = self._max_requests
            window = self._window_seconds
            dq = self._windows.get(key)
            cutoff = now - window
            valid = [t for t in dq] if dq else []
            valid = [t for t in valid if t >= cutoff]
            if not valid:
                return {
                    "limit": max_req,
                    "remaining": max_req,
                    "reset": int(now + window),
                }
            used = len(valid)
            remaining = max(0, max_req - used)
            reset_ts = int(valid[0] + window)
            return {"limit": max_req, "remaining": remaining, "reset": reset_ts}


stats = InMemoryStats()
tb_store = TokenBucketStore()
sw_store = SlidingWindowStore()
config_lock = threading.Lock()
tb_store.configure(10, 60.0)
sw_store.configure(10, 60.0)


class RateLimitMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        path = request.url.path
        ip = client_ip(request)

        if path.startswith("/api/"):
            stats.record_request(ip)

        limit_path = None
        if path == TOKEN_BUCKET_PATH or path.rstrip("/") == TOKEN_BUCKET_PATH:
            limit_path = "tb"
        elif path == SLIDING_WINDOW_PATH or path.rstrip("/") == SLIDING_WINDOW_PATH:
            limit_path = "sw"

        if limit_path is None:
            return await call_next(request)

        if limit_path == "tb":
            ok, remaining, reset_ts = tb_store.try_consume(ip)
            limit = tb_store.limit
        else:
            ok, remaining, reset_ts = sw_store.try_consume(ip)
            limit = sw_store.limit

        if not ok:
            stats.record_blocked(ip)
            return JSONResponse(
                status_code=429,
                content={"detail": "Rate limit exceeded"},
                headers={
                    "X-RateLimit-Limit": str(limit),
                    "X-RateLimit-Remaining": "0",
                    "X-RateLimit-Reset": str(reset_ts),
                },
            )

        response: Response = await call_next(request)
        response.headers["X-RateLimit-Limit"] = str(limit)
        response.headers["X-RateLimit-Remaining"] = str(remaining)
        response.headers["X-RateLimit-Reset"] = str(reset_ts)
        return response


app = FastAPI(title="Rate Limiter API", version="1.0.0")
app.add_middleware(RateLimitMiddleware)


@app.get("/api/limited/token-bucket")
def token_bucket_demo():
    return {"algorithm": "token_bucket", "message": "ok"}


@app.get("/api/limited/sliding-window")
def sliding_window_demo():
    return {"algorithm": "sliding_window", "message": "ok"}


@app.get("/api/unlimited")
def unlimited():
    return {"rate_limited": False, "message": "ok"}


@app.get("/api/rate-limit/status")
def rate_limit_status(client_id: str):
    tb = tb_store.peek(client_id)
    sw = sw_store.peek(client_id)
    return {
        "client_id": client_id,
        "token_bucket": tb,
        "sliding_window": sw,
    }


@app.post("/api/rate-limit/configure")
def configure_limits(body: RateLimitConfig):
    with config_lock:
        if body.algorithm in ("token_bucket", "both"):
            tb_store.configure(body.max_requests, float(body.window_seconds))
        if body.algorithm in ("sliding_window", "both"):
            sw_store.configure(body.max_requests, float(body.window_seconds))
    return {
        "ok": True,
        "algorithm": body.algorithm,
        "max_requests": body.max_requests,
        "window_seconds": body.window_seconds,
    }


@app.get("/api/rate-limit/stats")
def rate_limit_stats():
    return stats.snapshot()


@app.get("/")
def root():
    return {"service": "rate-limiter-api", "docs": "/docs"}
