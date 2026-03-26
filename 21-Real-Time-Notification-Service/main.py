import asyncio
import json
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, time, timezone
from typing import Any, Literal

from fastapi import Depends, FastAPI, HTTPException, Query, Request, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import JSON, delete, func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column
from sse_starlette.sse import EventSourceResponse

DATABASE_URL = "sqlite+aiosqlite:///./notifications.db"

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


DEFAULT_ENABLED_TYPES: list[str] = ["info", "warning", "error", "success"]


class Notification(Base):
    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(index=True)
    title: Mapped[str] = mapped_column()
    message: Mapped[str] = mapped_column()
    type: Mapped[str] = mapped_column()
    priority: Mapped[str] = mapped_column()
    is_read: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    read_at: Mapped[datetime | None] = mapped_column(nullable=True)


class UserPreference(Base):
    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    user_id: Mapped[str] = mapped_column(unique=True, index=True)
    enabled_types: Mapped[list[Any]] = mapped_column(JSON, default=lambda: list(DEFAULT_ENABLED_TYPES))
    delivery_method: Mapped[str] = mapped_column(default="both")
    quiet_hours_start: Mapped[str | None] = mapped_column(nullable=True)
    quiet_hours_end: Mapped[str | None] = mapped_column(nullable=True)


NotificationType = Literal["info", "warning", "error", "success"]
Priority = Literal["low", "normal", "high"]
DeliveryMethod = Literal["websocket", "sse", "both"]


def _parse_hm(value: str) -> time:
    parts = value.strip().split(":")
    if len(parts) != 2:
        raise ValueError("expected HH:MM")
    h, m = int(parts[0]), int(parts[1])
    return time(h, m)


def _in_quiet_hours(now: datetime, start: str | None, end: str | None) -> bool:
    if not start or not end:
        return False
    try:
        t_start, t_end = _parse_hm(start), _parse_hm(end)
    except ValueError:
        return False
    now_t = now.timetz() if now.tzinfo else now.time()
    if t_start <= t_end:
        return t_start <= now_t <= t_end
    return now_t >= t_start or now_t <= t_end


def _delivery_channels(pref: UserPreference | None) -> tuple[bool, bool]:
    if pref is None:
        return True, True
    dm = pref.delivery_method or "both"
    if dm == "websocket":
        return True, False
    if dm == "sse":
        return False, True
    return True, True


def _type_allowed(pref: UserPreference | None, notif_type: str) -> bool:
    if pref is None:
        return True
    types = pref.enabled_types or []
    if not types:
        return False
    return notif_type in types


def _should_push_realtime(pref: UserPreference | None, notif_type: str) -> tuple[bool, bool]:
    if not _type_allowed(pref, notif_type):
        return False, False
    now = datetime.now(timezone.utc)
    if _in_quiet_hours(now, pref.quiet_hours_start if pref else None, pref.quiet_hours_end if pref else None):
        return False, False
    return _delivery_channels(pref)


class NotificationHub:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._websockets: dict[str, set[WebSocket]] = defaultdict(set)
        self._sse_queues: dict[str, list[asyncio.Queue[dict[str, Any]]]] = defaultdict(list)

    async def register_ws(self, user_id: str, ws: WebSocket) -> None:
        async with self._lock:
            self._websockets[user_id].add(ws)

    async def unregister_ws(self, user_id: str, ws: WebSocket) -> None:
        async with self._lock:
            s = self._websockets.get(user_id)
            if not s:
                return
            s.discard(ws)
            if not s:
                del self._websockets[user_id]

    async def subscribe_sse(self, user_id: str) -> asyncio.Queue[dict[str, Any]]:
        q: asyncio.Queue[dict[str, Any]] = asyncio.Queue()
        async with self._lock:
            self._sse_queues[user_id].append(q)
        return q

    async def unsubscribe_sse(self, user_id: str, q: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            queues = self._sse_queues.get(user_id)
            if not queues:
                return
            if q in queues:
                queues.remove(q)
            if not queues:
                del self._sse_queues[user_id]

    async def publish(self, user_id: str, payload: dict[str, Any], push_ws: bool, push_sse: bool) -> None:
        dead: list[tuple[str, WebSocket]] = []
        sse_items: list[tuple[asyncio.Queue[dict[str, Any]], dict[str, Any]]] = []
        async with self._lock:
            if push_ws:
                for ws in list(self._websockets.get(user_id, set())):
                    try:
                        await ws.send_json(payload)
                    except Exception:
                        dead.append((user_id, ws))
            if push_sse:
                for q in list(self._sse_queues.get(user_id, [])):
                    sse_items.append((q, payload))
        for uid, ws in dead:
            await self.unregister_ws(uid, ws)
        for q, payload in sse_items:
            q.put_nowait(payload)


hub = NotificationHub()


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        yield session


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    yield
    await engine.dispose()


app = FastAPI(title="Real-Time Notification Service", lifespan=lifespan)


def _notification_to_dict(n: Notification) -> dict[str, Any]:
    return {
        "id": n.id,
        "user_id": n.user_id,
        "title": n.title,
        "message": n.message,
        "type": n.type,
        "priority": n.priority,
        "is_read": n.is_read,
        "created_at": n.created_at.isoformat() if n.created_at else None,
        "read_at": n.read_at.isoformat() if n.read_at else None,
    }


class NotificationCreate(BaseModel):
    user_id: str = Field(..., min_length=1)
    title: str = Field(..., min_length=1)
    message: str = Field(..., min_length=1)
    type: NotificationType = "info"
    priority: Priority = "normal"


class PreferenceUpdate(BaseModel):
    enabled_types: list[NotificationType] | None = None
    delivery_method: DeliveryMethod | None = None
    quiet_hours_start: str | None = None
    quiet_hours_end: str | None = None


async def _get_or_create_preferences(session: AsyncSession, user_id: str) -> UserPreference:
    r = await session.execute(select(UserPreference).where(UserPreference.user_id == user_id))
    pref = r.scalar_one_or_none()
    if pref:
        return pref
    pref = UserPreference(
        user_id=user_id,
        enabled_types=list(DEFAULT_ENABLED_TYPES),
        delivery_method="both",
        quiet_hours_start=None,
        quiet_hours_end=None,
    )
    session.add(pref)
    await session.commit()
    await session.refresh(pref)
    return pref


@app.post("/api/notifications", status_code=201)
async def create_notification(body: NotificationCreate, session: AsyncSession = Depends(get_db)):
    n = Notification(
        user_id=body.user_id,
        title=body.title,
        message=body.message,
        type=body.type,
        priority=body.priority,
    )
    session.add(n)
    await session.commit()
    await session.refresh(n)

    r = await session.execute(select(UserPreference).where(UserPreference.user_id == body.user_id))
    pref = r.scalar_one_or_none()
    push_ws, push_sse = _should_push_realtime(pref, body.type)
    payload = _notification_to_dict(n)
    await hub.publish(body.user_id, payload, push_ws, push_sse)
    return payload


@app.get("/api/notifications")
async def list_notifications(
    user_id: str = Query(..., min_length=1),
    read: bool | None = Query(None),
    notif_type: str | None = Query(None, alias="type"),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    session: AsyncSession = Depends(get_db),
):
    q = select(Notification).where(Notification.user_id == user_id)
    if read is not None:
        q = q.where(Notification.is_read == read)
    if notif_type:
        q = q.where(Notification.type == notif_type)
    q = q.order_by(Notification.created_at.desc()).offset(skip).limit(limit)
    r = await session.execute(q)
    rows = r.scalars().all()
    return [_notification_to_dict(n) for n in rows]


@app.get("/api/notifications/stats")
async def notification_stats(user_id: str = Query(..., min_length=1), session: AsyncSession = Depends(get_db)):
    total = await session.scalar(
        select(func.count()).select_from(Notification).where(Notification.user_id == user_id)
    )
    unread = await session.scalar(
        select(func.count()).select_from(Notification).where(Notification.user_id == user_id, Notification.is_read == False)  # noqa: E712
    )
    by_type_rows = await session.execute(
        select(Notification.type, func.count())
        .where(Notification.user_id == user_id)
        .group_by(Notification.type)
    )
    by_priority_rows = await session.execute(
        select(Notification.priority, func.count())
        .where(Notification.user_id == user_id)
        .group_by(Notification.priority)
    )
    return {
        "user_id": user_id,
        "total": int(total or 0),
        "unread": int(unread or 0),
        "by_type": {row[0]: row[1] for row in by_type_rows.all()},
        "by_priority": {row[0]: row[1] for row in by_priority_rows.all()},
    }


@app.patch("/api/notifications/read-all")
async def mark_all_read(user_id: str = Query(..., min_length=1), session: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc)
    await session.execute(
        update(Notification)
        .where(Notification.user_id == user_id, Notification.is_read == False)  # noqa: E712
        .values(is_read=True, read_at=now)
    )
    await session.commit()
    return {"ok": True, "user_id": user_id}


@app.patch("/api/notifications/{notification_id}/read")
async def mark_read(notification_id: int, session: AsyncSession = Depends(get_db)):
    now = datetime.now(timezone.utc)
    r = await session.execute(select(Notification).where(Notification.id == notification_id))
    n = r.scalar_one_or_none()
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
    n.is_read = True
    n.read_at = now
    await session.commit()
    await session.refresh(n)
    return _notification_to_dict(n)


@app.delete("/api/notifications/{notification_id}")
async def delete_notification(notification_id: int, session: AsyncSession = Depends(get_db)):
    r = await session.execute(select(Notification).where(Notification.id == notification_id))
    n = r.scalar_one_or_none()
    if not n:
        raise HTTPException(status_code=404, detail="Notification not found")
    await session.execute(delete(Notification).where(Notification.id == notification_id))
    await session.commit()
    return {"ok": True, "id": notification_id}


@app.get("/api/preferences/{user_id}")
async def get_preferences(user_id: str, session: AsyncSession = Depends(get_db)):
    pref = await _get_or_create_preferences(session, user_id)
    return {
        "user_id": pref.user_id,
        "enabled_types": list(pref.enabled_types or []),
        "delivery_method": pref.delivery_method,
        "quiet_hours_start": pref.quiet_hours_start,
        "quiet_hours_end": pref.quiet_hours_end,
    }


@app.put("/api/preferences/{user_id}")
async def update_preferences(user_id: str, body: PreferenceUpdate, session: AsyncSession = Depends(get_db)):
    pref = await _get_or_create_preferences(session, user_id)
    if body.enabled_types is not None:
        pref.enabled_types = list(body.enabled_types)
    if body.delivery_method is not None:
        pref.delivery_method = body.delivery_method
    if body.quiet_hours_start is not None:
        pref.quiet_hours_start = body.quiet_hours_start or None
    if body.quiet_hours_end is not None:
        pref.quiet_hours_end = body.quiet_hours_end or None
    await session.commit()
    await session.refresh(pref)
    return {
        "user_id": pref.user_id,
        "enabled_types": list(pref.enabled_types or []),
        "delivery_method": pref.delivery_method,
        "quiet_hours_start": pref.quiet_hours_start,
        "quiet_hours_end": pref.quiet_hours_end,
    }


@app.websocket("/ws/notifications/{user_id}")
async def websocket_notifications(websocket: WebSocket, user_id: str):
    await websocket.accept()
    await hub.register_ws(user_id, websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        pass
    finally:
        await hub.unregister_ws(user_id, websocket)


@app.get("/api/notifications/stream/{user_id}")
async def sse_notifications(user_id: str, request: Request):
    q = await hub.subscribe_sse(user_id)

    async def event_generator():
        try:
            while True:
                if await request.is_disconnected():
                    break
                try:
                    payload = await asyncio.wait_for(q.get(), timeout=20.0)
                    yield {"event": "notification", "data": json.dumps(payload)}
                except asyncio.TimeoutError:
                    yield {"event": "ping", "data": ""}
        finally:
            await hub.unsubscribe_sse(user_id, q)

    return EventSourceResponse(event_generator())


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
