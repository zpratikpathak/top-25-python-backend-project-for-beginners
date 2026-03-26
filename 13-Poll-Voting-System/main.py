import asyncio
import uuid
from collections import defaultdict
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Annotated, AsyncIterator, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, Request, Response, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    func,
    select,
)
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, selectinload
from sse_starlette import EventSourceResponse, JSONServerSentEvent

DATABASE_URL = "sqlite+aiosqlite:///./polls.db"


class Base(DeclarativeBase):
    pass


class Poll(Base):
    __tablename__ = "polls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    question: Mapped[str] = mapped_column(String(500), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    expires_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    options: Mapped[list["Option"]] = relationship(
        back_populates="poll", cascade="all, delete-orphan"
    )
    votes: Mapped[list["Vote"]] = relationship(
        back_populates="poll", cascade="all, delete-orphan"
    )


class Option(Base):
    __tablename__ = "options"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poll_id: Mapped[int] = mapped_column(
        ForeignKey("polls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    text: Mapped[str] = mapped_column(String(300), nullable=False)
    vote_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)

    poll: Mapped["Poll"] = relationship(back_populates="options")
    votes: Mapped[list["Vote"]] = relationship(
        back_populates="option", cascade="all, delete-orphan"
    )


class Vote(Base):
    __tablename__ = "votes"
    __table_args__ = (UniqueConstraint("poll_id", "voter_identifier", name="uq_vote_poll_voter"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    poll_id: Mapped[int] = mapped_column(
        ForeignKey("polls.id", ondelete="CASCADE"), nullable=False, index=True
    )
    option_id: Mapped[int] = mapped_column(
        ForeignKey("options.id", ondelete="CASCADE"), nullable=False, index=True
    )
    voter_identifier: Mapped[str] = mapped_column(String(255), nullable=False)
    voted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    poll: Mapped["Poll"] = relationship(back_populates="votes")
    option: Mapped["Option"] = relationship(back_populates="votes")


engine = create_async_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)
AsyncSessionLocal = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class PollEventBroadcaster:
    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._subs: dict[int, list[asyncio.Queue]] = defaultdict(list)

    async def subscribe(self, poll_id: int) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue()
        async with self._lock:
            self._subs[poll_id].append(q)
        return q

    async def unsubscribe(self, poll_id: int, q: asyncio.Queue) -> None:
        async with self._lock:
            if poll_id in self._subs and q in self._subs[poll_id]:
                self._subs[poll_id].remove(q)

    async def publish(self, poll_id: int, payload: dict) -> None:
        async with self._lock:
            queues = list(self._subs.get(poll_id, []))
        for q in queues:
            await q.put(payload)


broadcaster = PollEventBroadcaster()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    app.state.broadcaster = broadcaster
    yield
    await engine.dispose()


app = FastAPI(title="Poll Voting API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def poll_is_live(p: Poll, now: datetime) -> bool:
    if not p.is_active:
        return False
    if p.expires_at is not None and p.expires_at <= now:
        return False
    return True


def resolve_voter_identifier(request: Request) -> tuple[str, Optional[str]]:
    h = request.headers.get("X-Voter-Id") or request.headers.get("x-voter-id")
    if h and h.strip():
        return h.strip(), None
    c = request.cookies.get("voter_id")
    if c and c.strip():
        return c.strip(), None
    if fwd := request.headers.get("X-Forwarded-For"):
        vid = fwd.split(",")[0].strip()
    elif request.client:
        vid = request.client.host
    else:
        vid = str(uuid.uuid4())
    return vid, vid


async def get_db() -> AsyncIterator[AsyncSession]:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise


DbSession = Annotated[AsyncSession, Depends(get_db)]


class PollCreate(BaseModel):
    question: str = Field(..., min_length=1, max_length=500)
    options: list[str] = Field(..., min_length=2, max_length=50)
    expires_at: Optional[datetime] = None


class PollVoteBody(BaseModel):
    option_id: int


class OptionOut(BaseModel):
    id: int
    text: str
    vote_count: int

    model_config = {"from_attributes": True}


class PollDetailOut(BaseModel):
    id: int
    question: str
    created_at: datetime
    expires_at: Optional[datetime]
    is_active: bool
    is_live: bool
    options: list[OptionOut]
    total_votes: int

    model_config = {"from_attributes": True}


class PollListItem(BaseModel):
    id: int
    question: str
    created_at: datetime
    expires_at: Optional[datetime]
    is_active: bool
    is_live: bool
    total_votes: int

    model_config = {"from_attributes": True}


def build_results_payload(poll: Poll) -> dict:
    opts = [
        {"id": o.id, "text": o.text, "vote_count": o.vote_count}
        for o in sorted(poll.options, key=lambda x: x.id)
    ]
    total = sum(o.vote_count for o in poll.options)
    return {
        "poll_id": poll.id,
        "question": poll.question,
        "options": opts,
        "total_votes": total,
    }


@app.post("/api/polls", response_model=PollDetailOut, status_code=status.HTTP_201_CREATED)
async def create_poll(body: PollCreate, db: DbSession):
    if len(body.options) != len(set(body.options)):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Duplicate option texts")
    poll = Poll(question=body.question.strip(), expires_at=body.expires_at, is_active=True)
    for t in body.options:
        poll.options.append(Option(text=t.strip(), vote_count=0))
    db.add(poll)
    await db.flush()
    await db.refresh(poll, ["options"])
    now = utcnow()
    return PollDetailOut(
        id=poll.id,
        question=poll.question,
        created_at=poll.created_at,
        expires_at=poll.expires_at,
        is_active=poll.is_active,
        is_live=poll_is_live(poll, now),
        options=[OptionOut.model_validate(o) for o in sorted(poll.options, key=lambda x: x.id)],
        total_votes=0,
    )


@app.get("/api/polls", response_model=list[PollListItem])
async def list_polls(
    db: DbSession,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    active: Optional[bool] = None,
):
    now = utcnow()
    stmt = select(Poll).options(selectinload(Poll.options)).order_by(Poll.id.desc())
    if active is True:
        stmt = stmt.where(Poll.is_active.is_(True)).where(
            (Poll.expires_at.is_(None)) | (Poll.expires_at > now)
        )
    elif active is False:
        stmt = stmt.where(
            Poll.is_active.is_(False)
            | ((Poll.expires_at.is_not(None)) & (Poll.expires_at <= now))
        )
    stmt = stmt.offset(skip).limit(limit)
    polls = (await db.execute(stmt)).scalars().unique().all()
    return [
        PollListItem(
            id=p.id,
            question=p.question,
            created_at=p.created_at,
            expires_at=p.expires_at,
            is_active=p.is_active,
            is_live=poll_is_live(p, now),
            total_votes=sum(o.vote_count for o in p.options),
        )
        for p in polls
    ]


@app.get("/api/polls/{poll_id}", response_model=PollDetailOut)
async def get_poll(poll_id: int, db: DbSession):
    stmt = (
        select(Poll)
        .where(Poll.id == poll_id)
        .options(selectinload(Poll.options))
    )
    poll = (await db.execute(stmt)).scalar_one_or_none()
    if not poll:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Poll not found")
    now = utcnow()
    opts = sorted(poll.options, key=lambda x: x.id)
    tv = sum(o.vote_count for o in opts)
    return PollDetailOut(
        id=poll.id,
        question=poll.question,
        created_at=poll.created_at,
        expires_at=poll.expires_at,
        is_active=poll.is_active,
        is_live=poll_is_live(poll, now),
        options=[OptionOut.model_validate(o) for o in opts],
        total_votes=tv,
    )


@app.post("/api/polls/{poll_id}/vote")
async def cast_vote(
    poll_id: int,
    body: PollVoteBody,
    request: Request,
    response: Response,
    db: DbSession,
):
    voter_id, new_cookie = resolve_voter_identifier(request)
    stmt = (
        select(Poll)
        .where(Poll.id == poll_id)
        .options(selectinload(Poll.options))
    )
    poll = (await db.execute(stmt)).scalar_one_or_none()
    if not poll:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Poll not found")
    now = utcnow()
    if not poll_is_live(poll, now):
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Poll is not accepting votes")
    option = next((o for o in poll.options if o.id == body.option_id), None)
    if not option:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, detail="Invalid option_id for this poll")

    existing = await db.execute(
        select(Vote).where(Vote.poll_id == poll_id, Vote.voter_identifier == voter_id)
    )
    if existing.scalar_one_or_none():
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Already voted on this poll")

    vote = Vote(
        poll_id=poll_id,
        option_id=body.option_id,
        voter_identifier=voter_id,
    )
    option.vote_count += 1
    db.add(vote)
    try:
        await db.flush()
    except IntegrityError:
        await db.rollback()
        raise HTTPException(
            status.HTTP_409_CONFLICT, detail="Already voted on this poll"
        ) from None

    if new_cookie is not None:
        response.set_cookie(
            key="voter_id",
            value=new_cookie,
            max_age=60 * 60 * 24 * 365 * 5,
            httponly=True,
            samesite="lax",
        )

    payload = build_results_payload(poll)
    await broadcaster.publish(poll_id, payload)
    return {"ok": True, "results": payload}


@app.delete("/api/polls/{poll_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_poll(poll_id: int, db: DbSession):
    stmt = select(Poll).where(Poll.id == poll_id)
    poll = (await db.execute(stmt)).scalar_one_or_none()
    if not poll:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Poll not found")
    await db.delete(poll)


@app.get("/api/polls/{poll_id}/results/stream")
async def stream_results(poll_id: int, request: Request):
    async with AsyncSessionLocal() as check_db:
        found = await check_db.get(Poll, poll_id)
        if not found:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Poll not found")

    q = await broadcaster.subscribe(poll_id)

    async def gen() -> AsyncIterator[JSONServerSentEvent]:
        try:
            async with AsyncSessionLocal() as snap_db:
                stmt = (
                    select(Poll)
                    .where(Poll.id == poll_id)
                    .options(selectinload(Poll.options))
                )
                poll = (await snap_db.execute(stmt)).scalar_one_or_none()
                if poll:
                    yield JSONServerSentEvent(build_results_payload(poll), event="snapshot")
            while True:
                if await request.is_disconnected():
                    break
                try:
                    msg = await asyncio.wait_for(q.get(), timeout=30.0)
                    yield JSONServerSentEvent(msg, event="update")
                except asyncio.TimeoutError:
                    pass
        finally:
            await broadcaster.unsubscribe(poll_id, q)

    return EventSourceResponse(gen(), ping=15)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
