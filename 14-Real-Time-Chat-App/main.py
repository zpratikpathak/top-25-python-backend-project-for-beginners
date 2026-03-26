import json
from contextlib import asynccontextmanager
from datetime import datetime
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, ConfigDict, Field, field_validator
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    UniqueConstraint,
    create_engine,
    func,
    select,
)
from sqlalchemy.orm import Session, declarative_base, relationship, sessionmaker

DATABASE_URL = "sqlite:///./chat.db"
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class Room(Base):
    __tablename__ = "rooms"
    __table_args__ = (UniqueConstraint("name", name="uq_rooms_name"),)

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(128), nullable=False, index=True)
    description = Column(String(512), default="")
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    messages = relationship("Message", back_populates="room", cascade="all, delete-orphan")


class Message(Base):
    __tablename__ = "messages"

    id = Column(Integer, primary_key=True, index=True)
    room_id = Column(Integer, ForeignKey("rooms.id", ondelete="CASCADE"), nullable=False)
    username = Column(String(128), nullable=False)
    content = Column(String(4096), nullable=False)
    message_type = Column(String(16), nullable=False, default="chat")
    timestamp = Column(DateTime(timezone=True), server_default=func.now())

    room = relationship("Room", back_populates="messages")


class ConnectionManager:
    def __init__(self) -> None:
        self._rooms: dict[str, dict[str, WebSocket]] = {}

    def connect(self, room_name: str, username: str, websocket: WebSocket) -> None:
        if room_name not in self._rooms:
            self._rooms[room_name] = {}
        self._rooms[room_name][username] = websocket

    def disconnect(self, room_name: str, username: str) -> None:
        room = self._rooms.get(room_name)
        if not room:
            return
        room.pop(username, None)
        if not room:
            del self._rooms[room_name]

    def get_socket(self, room_name: str, username: str) -> Optional[WebSocket]:
        return self._rooms.get(room_name, {}).get(username)

    def active_usernames(self, room_name: str) -> list[str]:
        return sorted(self._rooms.get(room_name, {}).keys())

    def active_count(self, room_name: str) -> int:
        return len(self._rooms.get(room_name, {}))

    async def broadcast(
        self, room_name: str, payload: dict, exclude_username: Optional[str] = None
    ) -> None:
        room = self._rooms.get(room_name, {})
        text = json.dumps(payload, default=str)
        for user, ws in list(room.items()):
            if exclude_username and user == exclude_username:
                continue
            try:
                await ws.send_text(text)
            except Exception:
                pass


manager = ConnectionManager()


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DbSession = Annotated[Session, Depends(get_db)]


class RoomCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=128)
    description: str = Field(default="", max_length=512)

    @field_validator("name")
    @classmethod
    def name_strip(cls, v: str) -> str:
        s = v.strip()
        if not s:
            raise ValueError("name cannot be blank")
        return s

    @field_validator("description")
    @classmethod
    def description_strip(cls, v: str) -> str:
        return v.strip()


class RoomOut(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    name: str
    description: str
    created_at: Optional[datetime]
    active_users: int


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Real-Time Chat API", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_room_by_name(db: Session, room_name: str) -> Optional[Room]:
    return db.scalars(select(Room).where(Room.name == room_name)).first()


@app.post("/api/rooms", response_model=RoomOut)
def create_room(body: RoomCreate, db: DbSession):
    if get_room_by_name(db, body.name):
        raise HTTPException(status_code=409, detail="Room name already exists")
    room = Room(name=body.name, description=body.description)
    db.add(room)
    db.commit()
    db.refresh(room)
    return RoomOut(
        id=room.id,
        name=room.name,
        description=room.description or "",
        created_at=room.created_at,
        active_users=manager.active_count(room.name),
    )


@app.get("/api/rooms", response_model=list[RoomOut])
def list_rooms(db: DbSession):
    rooms = db.scalars(select(Room).order_by(Room.created_at.desc())).all()
    return [
        RoomOut(
            id=r.id,
            name=r.name,
            description=r.description or "",
            created_at=r.created_at,
            active_users=manager.active_count(r.name),
        )
        for r in rooms
    ]


@app.get("/api/rooms/{room_name}/history")
def room_history(
    room_name: str,
    db: DbSession,
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    room = get_room_by_name(db, room_name)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    q = (
        select(Message)
        .where(Message.room_id == room.id)
        .order_by(Message.timestamp.desc())
        .offset(skip)
        .limit(limit)
    )
    rows = db.scalars(q).all()
    total = (
        db.scalar(
            select(func.count()).select_from(Message).where(Message.room_id == room.id)
        )
        or 0
    )
    items = [
        {
            "id": m.id,
            "username": m.username,
            "content": m.content,
            "message_type": m.message_type,
            "timestamp": m.timestamp.isoformat() if m.timestamp else None,
        }
        for m in reversed(rows)
    ]
    return {"total": total, "skip": skip, "limit": limit, "messages": items}


@app.get("/api/rooms/{room_name}/users")
def room_users(room_name: str, db: DbSession):
    room = get_room_by_name(db, room_name)
    if not room:
        raise HTTPException(status_code=404, detail="Room not found")
    return {"room_name": room_name, "users": manager.active_usernames(room_name)}


def persist_message(
    db: Session,
    room_id: int,
    username: str,
    content: str,
    message_type: str,
) -> Message:
    msg = Message(
        room_id=room_id,
        username=username,
        content=content,
        message_type=message_type,
    )
    db.add(msg)
    db.commit()
    db.refresh(msg)
    return msg


@app.websocket("/ws/{room_name}")
async def websocket_chat(websocket: WebSocket, room_name: str):
    username = websocket.query_params.get("username")
    if not username or not username.strip():
        await websocket.close(code=4400)
        return
    username = username.strip()[:128]

    db = SessionLocal()
    try:
        room = get_room_by_name(db, room_name)
        if not room:
            await websocket.close(code=4404)
            return

        await websocket.accept()

        existing = manager.get_socket(room_name, username)
        if existing and existing != websocket:
            try:
                await existing.close(code=4409)
            except Exception:
                pass

        manager.connect(room_name, username, websocket)

        join_msg = persist_message(db, room.id, username, f"{username} joined", "join")
        payload = {
            "type": "join",
            "username": username,
            "content": join_msg.content,
            "timestamp": join_msg.timestamp.isoformat() if join_msg.timestamp else None,
        }
        await manager.broadcast(room_name, payload)

        try:
            while True:
                raw = await websocket.receive_text()
                chat_msg = persist_message(db, room.id, username, raw, "chat")
                out = {
                    "type": "chat",
                    "username": username,
                    "content": chat_msg.content,
                    "timestamp": chat_msg.timestamp.isoformat()
                    if chat_msg.timestamp
                    else None,
                }
                await manager.broadcast(room_name, out)
        except WebSocketDisconnect:
            pass
        finally:
            manager.disconnect(room_name, username)
            leave_msg = persist_message(
                db, room.id, username, f"{username} left", "leave"
            )
            await manager.broadcast(
                room_name,
                {
                    "type": "leave",
                    "username": username,
                    "content": leave_msg.content,
                    "timestamp": leave_msg.timestamp.isoformat()
                    if leave_msg.timestamp
                    else None,
                },
            )
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
