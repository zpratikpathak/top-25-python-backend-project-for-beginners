import asyncio
import secrets
import time
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from enum import Enum

import httpx
from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean,
    DateTime,
    Float,
    Integer,
    String,
    case,
    delete,
    func,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

DATABASE_URL = "sqlite+aiosqlite:///./gateway.db"
DEFAULT_RATE_LIMIT_RPM = 100
HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailers",
    "transfer-encoding",
    "upgrade",
    "host",
    "content-length",
}


class Base(DeclarativeBase):
    pass


class ServiceRow(Base):
    __tablename__ = "services"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    base_url: Mapped[str] = mapped_column(String(512))
    health_check_path: Mapped[str] = mapped_column(String(256), default="/health")
    is_healthy: Mapped[bool] = mapped_column(Boolean, default=True)
    rate_limit_per_minute: Mapped[int | None] = mapped_column(Integer, nullable=True)
    circuit_failure_threshold: Mapped[int] = mapped_column(Integer, default=5)
    circuit_recovery_seconds: Mapped[int] = mapped_column(Integer, default=60)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class ApiKeyRow(Base):
    __tablename__ = "api_keys"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    key: Mapped[str] = mapped_column(String(128), unique=True, index=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=lambda: datetime.now(timezone.utc))


class RequestLogRow(Base):
    __tablename__ = "request_logs"
    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    ts: Mapped[datetime] = mapped_column(DateTime(timezone=True), index=True)
    service_name: Mapped[str] = mapped_column(String(128), index=True)
    path: Mapped[str] = mapped_column(String(1024))
    method: Mapped[str] = mapped_column(String(16))
    status_code: Mapped[int] = mapped_column(Integer)
    latency_ms: Mapped[float] = mapped_column(Float)
    error: Mapped[bool] = mapped_column(Boolean, default=False)
    client_id: Mapped[str | None] = mapped_column(String(128), nullable=True)


engine = create_async_engine(DATABASE_URL, echo=False)
SessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class CircuitState(str, Enum):
    CLOSED = "closed"
    OPEN = "open"
    HALF_OPEN = "half_open"


class CircuitBreakerMem:
    __slots__ = ("state", "failures", "opened_at", "half_open_slot", "lock")

    def __init__(self) -> None:
        self.state = CircuitState.CLOSED
        self.failures = 0
        self.opened_at: float | None = None
        self.half_open_slot = True
        self.lock = asyncio.Lock()

    async def allow(self, threshold: int, recovery_s: int) -> tuple[bool, str]:
        async with self.lock:
            now = time.monotonic()
            if self.state == CircuitState.CLOSED:
                return True, ""
            if self.state == CircuitState.OPEN:
                if self.opened_at is not None and now - self.opened_at >= recovery_s:
                    self.state = CircuitState.HALF_OPEN
                    self.half_open_slot = True
                else:
                    return False, "circuit_open"
            if self.state == CircuitState.HALF_OPEN:
                if self.half_open_slot:
                    self.half_open_slot = False
                    return True, "half_open_probe"
                return False, "circuit_half_open"
            return True, ""

    async def on_success(self) -> None:
        async with self.lock:
            self.failures = 0
            self.state = CircuitState.CLOSED
            self.half_open_slot = True
            self.opened_at = None

    async def on_failure(self, threshold: int) -> None:
        async with self.lock:
            if self.state == CircuitState.HALF_OPEN:
                self.state = CircuitState.OPEN
                self.opened_at = time.monotonic()
                self.half_open_slot = True
                return
            self.failures += 1
            if self.failures >= threshold:
                self.state = CircuitState.OPEN
                self.opened_at = time.monotonic()


circuit_breakers: dict[str, CircuitBreakerMem] = {}
rate_buckets: dict[tuple[str, str], list[float]] = {}
rate_lock = asyncio.Lock()


def get_circuit(name: str) -> CircuitBreakerMem:
    if name not in circuit_breakers:
        circuit_breakers[name] = CircuitBreakerMem()
    return circuit_breakers[name]


async def check_rate_limit(client_id: str, service_name: str, limit_rpm: int) -> bool:
    key = (client_id, service_name)
    now = time.monotonic()
    window = 60.0
    async with rate_lock:
        bucket = rate_buckets.setdefault(key, [])
        cutoff = now - window
        while bucket and bucket[0] < cutoff:
            bucket.pop(0)
        if len(bucket) >= limit_rpm:
            return False
        bucket.append(now)
    return True


async def get_db() -> AsyncSession:
    async with SessionLocal() as session:
        yield session


class ServiceCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    base_url: str
    health_check_path: str = Field(default="/health", max_length=256)
    rate_limit_per_minute: int | None = Field(default=None, ge=1)
    circuit_failure_threshold: int = Field(default=5, ge=1)
    circuit_recovery_seconds: int = Field(default=60, ge=1)


class ServiceOut(BaseModel):
    name: str
    base_url: str
    health_check_path: str
    is_healthy: bool
    rate_limit_per_minute: int | None
    circuit_failure_threshold: int
    circuit_recovery_seconds: int


class TokenResponse(BaseModel):
    token: str


class StatsPerService(BaseModel):
    service_name: str
    request_count: int
    error_count: int
    avg_latency_ms: float | None


class GatewayStats(BaseModel):
    total_requests: int
    total_errors: int
    error_rate: float
    avg_latency_ms: float | None
    per_service: list[StatsPerService]


class LogEntry(BaseModel):
    id: int
    timestamp: datetime
    service_name: str
    path: str
    method: str
    status_code: int
    latency_ms: float
    error: bool
    client_id: str | None


class LogsPage(BaseModel):
    items: list[LogEntry]
    total: int
    offset: int
    limit: int


http_client: httpx.AsyncClient | None = None


async def health_check_loop() -> None:
    while True:
        await asyncio.sleep(15)
        if http_client is None:
            continue
        async with SessionLocal() as db:
            res = await db.execute(select(ServiceRow))
            services = res.scalars().all()
            for svc in services:
                url = svc.base_url.rstrip("/") + (
                    svc.health_check_path if svc.health_check_path.startswith("/") else "/" + svc.health_check_path
                )
                ok = False
                try:
                    r = await http_client.get(url, timeout=httpx.Timeout(5.0))
                    ok = r.status_code < 500
                except Exception:
                    ok = False
                svc.is_healthy = ok
            await db.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    global http_client
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    http_client = httpx.AsyncClient(follow_redirects=True, timeout=httpx.Timeout(60.0))
    task = asyncio.create_task(health_check_loop())
    yield
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass
    await http_client.aclose()
    await engine.dispose()


app = FastAPI(title="API Gateway", lifespan=lifespan)


@app.middleware("http")
async def gateway_api_key_middleware(request: Request, call_next):
    path = request.url.path
    if path.startswith("/gateway/"):
        key = request.headers.get("x-api-key")
        if not key:
            return JSONResponse(status_code=401, content={"detail": "Missing X-API-Key header"})
        async with SessionLocal() as db:
            r = await db.execute(select(ApiKeyRow).where(ApiKeyRow.key == key))
            if r.scalar_one_or_none() is None:
                return JSONResponse(status_code=401, content={"detail": "Invalid API key"})
    return await call_next(request)


@app.post("/api/services", response_model=ServiceOut)
async def register_service(body: ServiceCreate, db: AsyncSession = Depends(get_db)):
    row = ServiceRow(
        name=body.name.strip(),
        base_url=body.base_url.rstrip("/"),
        health_check_path=body.health_check_path or "/health",
        rate_limit_per_minute=body.rate_limit_per_minute,
        circuit_failure_threshold=body.circuit_failure_threshold,
        circuit_recovery_seconds=body.circuit_recovery_seconds,
    )
    db.add(row)
    try:
        await db.commit()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(status_code=409, detail="Service name already exists") from None
    await db.refresh(row)
    get_circuit(row.name)
    return ServiceOut(
        name=row.name,
        base_url=row.base_url,
        health_check_path=row.health_check_path,
        is_healthy=row.is_healthy,
        rate_limit_per_minute=row.rate_limit_per_minute,
        circuit_failure_threshold=row.circuit_failure_threshold,
        circuit_recovery_seconds=row.circuit_recovery_seconds,
    )


@app.get("/api/services", response_model=list[ServiceOut])
async def list_services(db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(ServiceRow).order_by(ServiceRow.name))
    rows = res.scalars().all()
    return [
        ServiceOut(
            name=r.name,
            base_url=r.base_url,
            health_check_path=r.health_check_path,
            is_healthy=r.is_healthy,
            rate_limit_per_minute=r.rate_limit_per_minute,
            circuit_failure_threshold=r.circuit_failure_threshold,
            circuit_recovery_seconds=r.circuit_recovery_seconds,
        )
        for r in rows
    ]


@app.delete("/api/services/{name}")
async def delete_service(name: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(ServiceRow).where(ServiceRow.name == name))
    row = res.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Service not found")
    await db.execute(delete(ServiceRow).where(ServiceRow.id == row.id))
    await db.commit()
    circuit_breakers.pop(name, None)
    return {"ok": True}


@app.get("/api/services/{name}/health")
async def service_health(name: str, db: AsyncSession = Depends(get_db)):
    res = await db.execute(select(ServiceRow).where(ServiceRow.name == name))
    row = res.scalar_one_or_none()
    if not row:
        raise HTTPException(status_code=404, detail="Service not found")
    if http_client is None:
        raise HTTPException(status_code=503, detail="Gateway not ready")
    url = row.base_url.rstrip("/") + (
        row.health_check_path if row.health_check_path.startswith("/") else "/" + row.health_check_path
    )
    try:
        r = await http_client.get(url, timeout=httpx.Timeout(5.0))
        live_ok = r.status_code < 500
    except Exception as e:
        return {"name": name, "reachable": False, "status_code": None, "error": str(e)}
    return {"name": name, "reachable": True, "status_code": r.status_code, "registry_healthy": row.is_healthy}


@app.post("/api/auth/token", response_model=TokenResponse)
async def issue_token(db: AsyncSession = Depends(get_db)):
    token = secrets.token_urlsafe(32)
    db.add(ApiKeyRow(key=token))
    await db.commit()
    return TokenResponse(token=token)


@app.get("/api/gateway/stats", response_model=GatewayStats)
async def gateway_stats(db: AsyncSession = Depends(get_db)):
    total = await db.execute(select(func.count(RequestLogRow.id)))
    total_requests = int(total.scalar() or 0)
    err_q = await db.execute(select(func.count(RequestLogRow.id)).where(RequestLogRow.error == True))  # noqa: E712
    total_errors = int(err_q.scalar() or 0)
    avg_q = await db.execute(select(func.avg(RequestLogRow.latency_ms)))
    avg_all = avg_q.scalar()
    per_rows = await db.execute(
        select(
            RequestLogRow.service_name,
            func.count(RequestLogRow.id),
            func.sum(case((RequestLogRow.error == True, 1), else_=0)),  # noqa: E712
            func.avg(RequestLogRow.latency_ms),
        ).group_by(RequestLogRow.service_name)
    )
    per_service = []
    for sn, cnt, errs, avgl in per_rows.all():
        ec = int(errs or 0)
        per_service.append(
            StatsPerService(
                service_name=sn,
                request_count=int(cnt),
                error_count=ec,
                avg_latency_ms=float(avgl) if avgl is not None else None,
            )
        )
    er = (total_errors / total_requests) if total_requests else 0.0
    return GatewayStats(
        total_requests=total_requests,
        total_errors=total_errors,
        error_rate=round(er, 4),
        avg_latency_ms=float(avg_all) if avg_all is not None else None,
        per_service=per_service,
    )


@app.get("/api/gateway/logs", response_model=LogsPage)
async def gateway_logs(
    offset: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    db: AsyncSession = Depends(get_db),
):
    cnt_q = await db.execute(select(func.count(RequestLogRow.id)))
    total = int(cnt_q.scalar() or 0)
    res = await db.execute(
        select(RequestLogRow).order_by(RequestLogRow.id.desc()).offset(offset).limit(limit)
    )
    rows = res.scalars().all()
    items = [
        LogEntry(
            id=r.id,
            timestamp=r.ts,
            service_name=r.service_name,
            path=r.path,
            method=r.method,
            status_code=r.status_code,
            latency_ms=r.latency_ms,
            error=r.error,
            client_id=r.client_id,
        )
        for r in rows
    ]
    return LogsPage(items=items, total=total, offset=offset, limit=limit)


def filter_request_headers(headers: Headers) -> dict[str, str]:
    out: dict[str, str] = {}
    for k, v in headers.items():
        lk = k.lower()
        if lk in HOP_BY_HOP_HEADERS or lk == "x-api-key":
            continue
        out[k] = v
    return out


@app.api_route(
    "/gateway/{service_name}/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def gateway_proxy(service_name: str, path: str, request: Request):
    if http_client is None:
        raise HTTPException(status_code=503, detail="Gateway not ready")
    async with SessionLocal() as db:
        res = await db.execute(select(ServiceRow).where(ServiceRow.name == service_name))
        svc = res.scalar_one_or_none()
    if not svc:
        raise HTTPException(status_code=404, detail="Unknown service")
    client_key = request.headers.get("x-api-key") or (
        request.client.host if request.client else "unknown"
    )
    limit_rpm = svc.rate_limit_per_minute if svc.rate_limit_per_minute is not None else DEFAULT_RATE_LIMIT_RPM
    if not await check_rate_limit(client_key, service_name, limit_rpm):
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
    cb = get_circuit(service_name)
    ok_cb, reason = await cb.allow(svc.circuit_failure_threshold, svc.circuit_recovery_seconds)
    if not ok_cb:
        raise HTTPException(status_code=503, detail=f"Circuit breaker: {reason}")
    base = svc.base_url.rstrip("/")
    sub = path.lstrip("/")
    target = f"{base}/{sub}" if sub else base
    if request.url.query:
        target = f"{target}?{request.url.query}"
    body = await request.body()
    fwd_headers = filter_request_headers(request.headers)
    method = request.method.upper()
    t0 = time.perf_counter()
    status_code = 502
    err_flag = True
    try:
        resp = await http_client.request(
            method,
            target,
            headers=fwd_headers,
            content=body if body else None,
        )
        status_code = resp.status_code
        err_flag = status_code >= 500
        if status_code < 500:
            await cb.on_success()
        else:
            await cb.on_failure(svc.circuit_failure_threshold)
    except httpx.RequestError:
        await cb.on_failure(svc.circuit_failure_threshold)
        latency_ms = (time.perf_counter() - t0) * 1000
        async with SessionLocal() as log_db:
            log_db.add(
                RequestLogRow(
                    ts=datetime.now(timezone.utc),
                    service_name=service_name,
                    path=path,
                    method=method,
                    status_code=502,
                    latency_ms=latency_ms,
                    error=True,
                    client_id=client_key[:128] if isinstance(client_key, str) else None,
                )
            )
            await log_db.commit()
        raise HTTPException(status_code=502, detail="Upstream unreachable") from None
    latency_ms = (time.perf_counter() - t0) * 1000
    async with SessionLocal() as log_db:
        log_db.add(
            RequestLogRow(
                ts=datetime.now(timezone.utc),
                service_name=service_name,
                path=path,
                method=method,
                status_code=status_code,
                latency_ms=latency_ms,
                error=err_flag,
                client_id=client_key[:128] if isinstance(client_key, str) else None,
            )
        )
        await log_db.commit()
    out_headers = {
        k: v
        for k, v in resp.headers.items()
        if k.lower() not in HOP_BY_HOP_HEADERS and k.lower() != "content-encoding"
    }
    return Response(content=resp.content, status_code=resp.status_code, headers=out_headers)


@app.api_route("/gateway/{service_name}", methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"])
async def gateway_proxy_root(service_name: str, request: Request):
    return await gateway_proxy(service_name, "", request)
