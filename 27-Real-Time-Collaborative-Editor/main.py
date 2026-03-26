from __future__ import annotations

import json
import uuid
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from pydantic import BaseModel, Field
from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, create_engine, event, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker


DATABASE_URL = "sqlite:///./collab_editor.db"
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
)


@event.listens_for(engine, "connect")
def _sqlite_foreign_keys(dbapi_conn, connection_record):
    cur = dbapi_conn.cursor()
    cur.execute("PRAGMA foreign_keys=ON")
    cur.close()
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class OpType(str, Enum):
    insert = "insert"
    delete = "delete"


class Document(Base):
    __tablename__ = "documents"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(512), nullable=False)
    content: Mapped[str] = mapped_column(Text, default="")
    version: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    operations: Mapped[list["Operation"]] = relationship(
        "Operation",
        back_populates="document",
        order_by="Operation.version",
        cascade="all, delete-orphan",
    )


class Operation(Base):
    __tablename__ = "operations"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    document_id: Mapped[int] = mapped_column(
        ForeignKey("documents.id", ondelete="CASCADE"), nullable=False
    )
    user: Mapped[str] = mapped_column(String(256), nullable=False)
    op_type: Mapped[str] = mapped_column(String(32), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    text: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    delete_length: Mapped[Optional[int]] = mapped_column(Integer, nullable=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )

    document: Mapped["Document"] = relationship("Document", back_populates="operations")


@dataclass
class InsertOp:
    position: int
    text: str

    def copy(self) -> "InsertOp":
        return InsertOp(self.position, self.text)


@dataclass
class DeleteOp:
    position: int
    length: int

    def copy(self) -> "DeleteOp":
        return DeleteOp(self.position, self.length)


def transform_insert_against_insert(client: InsertOp, server: InsertOp) -> InsertOp:
    out = client.copy()
    if out.position > server.position:
        out.position += len(server.text)
    elif out.position == server.position:
        out.position += len(server.text)
    return out


def transform_insert_against_delete(client: InsertOp, server: DeleteOp) -> InsertOp:
    out = client.copy()
    ps, ls = server.position, server.length
    if out.position <= ps:
        return out
    if out.position >= ps + ls:
        out.position -= ls
        return out
    out.position = ps
    return out


def transform_delete_against_insert(client: DeleteOp, server: InsertOp) -> DeleteOp:
    out = client.copy()
    ps = server.position
    lt = len(server.text)
    end = out.position + out.length
    if ps <= out.position:
        out.position += lt
    elif ps < end:
        out.length += lt
    return out


def transform_delete_against_delete(client: DeleteOp, server: DeleteOp) -> Optional[DeleteOp]:
    ps, ls = server.position, server.length
    pc, lc = client.position, client.length
    c_end = pc + lc
    s_end = ps + ls

    if pc >= s_end:
        return DeleteOp(pc - ls, lc)
    if c_end <= ps:
        return DeleteOp(pc, lc)
    if pc >= ps and c_end <= s_end:
        return None
    if pc < ps and c_end > s_end:
        return DeleteOp(pc, ps - pc + (c_end - s_end))
    if pc < ps:
        if c_end <= s_end:
            return DeleteOp(pc, ps - pc)
        return DeleteOp(pc, c_end - s_end)
    if pc >= ps and c_end > s_end:
        return DeleteOp(ps, c_end - s_end)
    return DeleteOp(pc, max(0, ps - pc))


def op_from_row(row: Operation) -> InsertOp | DeleteOp:
    if row.op_type == OpType.insert.value:
        return InsertOp(row.position, row.text or "")
    return DeleteOp(row.position, row.delete_length or 0)


def transform_client_op_against_server_op(
    client: InsertOp | DeleteOp, server_row: Operation
) -> InsertOp | DeleteOp | None:
    s = op_from_row(server_row)
    if isinstance(client, InsertOp) and isinstance(s, InsertOp):
        return transform_insert_against_insert(client, s)
    if isinstance(client, InsertOp) and isinstance(s, DeleteOp):
        return transform_insert_against_delete(client, s)
    if isinstance(client, DeleteOp) and isinstance(s, InsertOp):
        return transform_delete_against_insert(client, s)
    if isinstance(client, DeleteOp) and isinstance(s, DeleteOp):
        return transform_delete_against_delete(client, s)
    return client


def apply_op_to_string(content: str, op: InsertOp | DeleteOp) -> str:
    if isinstance(op, InsertOp):
        p = max(0, min(op.position, len(content)))
        return content[:p] + op.text + content[p:]
    p = max(0, min(op.position, len(content)))
    end = min(p + op.length, len(content))
    return content[:p] + content[end:]


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="Real-Time Collaborative Editor", lifespan=lifespan)


class CreateDocumentBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=512)
    content: str = ""


class UpdateDocumentBody(BaseModel):
    title: str = Field(..., min_length=1, max_length=512)


class DocumentOut(BaseModel):
    id: int
    title: str
    content: str
    version: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class DocumentSummary(BaseModel):
    id: int
    title: str
    version: int
    created_at: datetime
    updated_at: datetime

    class Config:
        from_attributes = True


class PaginatedDocuments(BaseModel):
    items: list[DocumentSummary]
    total: int
    skip: int
    limit: int


class OperationOut(BaseModel):
    id: int
    document_id: int
    user: str
    op_type: str
    position: int
    text: Optional[str]
    delete_length: Optional[int]
    version: int
    created_at: datetime

    class Config:
        from_attributes = True


class CollaboratorOut(BaseModel):
    username: str
    cursor_position: Optional[int]


class ConnectionInfo:
    __slots__ = ("ws", "username", "cursor_position")

    def __init__(self, ws: WebSocket, username: str):
        self.ws = ws
        self.username = username
        self.cursor_position: Optional[int] = None


class ConnectionManager:
    def __init__(self) -> None:
        self._rooms: dict[int, dict[str, ConnectionInfo]] = {}

    def connect(self, document_id: int, conn_id: str, info: ConnectionInfo) -> None:
        self._rooms.setdefault(document_id, {})[conn_id] = info

    def disconnect(self, document_id: int, conn_id: str) -> None:
        room = self._rooms.get(document_id)
        if not room:
            return
        room.pop(conn_id, None)
        if not room:
            self._rooms.pop(document_id, None)

    def set_cursor(self, document_id: int, conn_id: str, position: int) -> None:
        room = self._rooms.get(document_id)
        if room and conn_id in room:
            room[conn_id].cursor_position = position

    def collaborators(self, document_id: int) -> list[CollaboratorOut]:
        room = self._rooms.get(document_id, {})
        return [
            CollaboratorOut(username=c.username, cursor_position=c.cursor_position)
            for c in room.values()
        ]

    async def broadcast_json(
        self,
        document_id: int,
        message: dict[str, Any],
        exclude_conn_id: Optional[str] = None,
    ) -> None:
        room = self._rooms.get(document_id, {})
        dead: list[str] = []
        for cid, info in room.items():
            if cid == exclude_conn_id:
                continue
            try:
                await info.ws.send_json(message)
            except Exception:
                dead.append(cid)
        for cid in dead:
            self.disconnect(document_id, cid)


manager = ConnectionManager()


@app.post("/api/documents", response_model=DocumentOut)
def create_document(body: CreateDocumentBody, db: Session = Depends(get_db)):
    now = datetime.now(timezone.utc)
    initial = body.content or ""
    doc = Document(
        title=body.title,
        content=initial,
        version=0,
        created_at=now,
        updated_at=now,
    )
    db.add(doc)
    db.flush()
    if initial:
        db.add(
            Operation(
                document_id=doc.id,
                user="__system__",
                op_type=OpType.insert.value,
                position=0,
                text=initial,
                delete_length=None,
                version=1,
            )
        )
        doc.version = 1
    db.commit()
    db.refresh(doc)
    return doc


@app.get("/api/documents", response_model=PaginatedDocuments)
def list_documents(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    total = db.scalar(select(func.count()).select_from(Document)) or 0
    rows = (
        db.execute(select(Document).order_by(Document.id.desc()).offset(skip).limit(limit))
        .scalars()
        .all()
    )
    items = [
        DocumentSummary(
            id=r.id,
            title=r.title,
            version=r.version,
            created_at=r.created_at,
            updated_at=r.updated_at,
        )
        for r in rows
    ]
    return PaginatedDocuments(items=items, total=total, skip=skip, limit=limit)


@app.get("/api/documents/{document_id}", response_model=DocumentOut)
def get_document(document_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    return doc


@app.put("/api/documents/{document_id}", response_model=DocumentOut)
def update_document(
    document_id: int, body: UpdateDocumentBody, db: Session = Depends(get_db)
):
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    doc.title = body.title
    doc.updated_at = datetime.now(timezone.utc)
    db.commit()
    db.refresh(doc)
    return doc


@app.delete("/api/documents/{document_id}")
def delete_document(document_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    db.delete(doc)
    db.commit()
    return {"ok": True}


@app.get("/api/documents/{document_id}/history", response_model=list[OperationOut])
def get_history(document_id: int, db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    ops = (
        db.execute(
            select(Operation)
            .where(Operation.document_id == document_id)
            .order_by(Operation.version.asc())
        )
        .scalars()
        .all()
    )
    return ops


def replay_from_empty(db: Session, document_id: int, up_to_version: int) -> str:
    ops = (
        db.execute(
            select(Operation)
            .where(
                Operation.document_id == document_id,
                Operation.version <= up_to_version,
            )
            .order_by(Operation.version.asc())
        )
        .scalars()
        .all()
    )
    content = ""
    for o in ops:
        content = apply_op_to_string(content, op_from_row(o))
    return content


def document_content_at_version(db: Session, doc: Document, version: int) -> str:
    if version == 0:
        has_ops = (
            db.execute(
                select(func.count()).select_from(Operation).where(Operation.document_id == doc.id)
            ).scalar()
            or 0
        )
        if has_ops == 0:
            return doc.content
        return ""
    return replay_from_empty(db, doc.id, version)


@app.get("/api/documents/{document_id}/version/{version}", response_model=DocumentOut)
def get_document_at_version(document_id: int, version: int, db: Session = Depends(get_db)):
    doc = db.get(Document, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="Document not found")
    if version < 0 or version > doc.version:
        raise HTTPException(status_code=404, detail="Version out of range")
    content = document_content_at_version(db, doc, version)
    return DocumentOut(
        id=doc.id,
        title=doc.title,
        content=content,
        version=version,
        created_at=doc.created_at,
        updated_at=doc.updated_at,
    )


@app.get("/api/documents/{document_id}/collaborators", response_model=list[CollaboratorOut])
def get_collaborators(document_id: int, db: Session = Depends(get_db)):
    if not db.get(Document, document_id):
        raise HTTPException(status_code=404, detail="Document not found")
    return manager.collaborators(document_id)


@app.websocket("/ws/documents/{document_id}")
async def websocket_document(
    websocket: WebSocket,
    document_id: int,
    username: str = Query(..., min_length=1, max_length=256),
):
    db = SessionLocal()
    conn_id: Optional[str] = None
    try:
        doc = db.get(Document, document_id)
        if not doc:
            await websocket.close(code=4404)
            return
        await websocket.accept()
        conn_id = str(uuid.uuid4())
        manager.connect(document_id, conn_id, ConnectionInfo(websocket, username))
        await websocket.send_json(
            {
                "type": "sync",
                "content": doc.content,
                "version": doc.version,
                "title": doc.title,
            }
        )
        presences = manager.collaborators(document_id)
        await websocket.send_json({"type": "presence", "collaborators": [p.model_dump() for p in presences]})
        await manager.broadcast_json(
            document_id,
            {
                "type": "user_joined",
                "username": username,
                "collaborators": [p.model_dump() for p in manager.collaborators(document_id)],
            },
            exclude_conn_id=conn_id,
        )

        while True:
            raw = await websocket.receive_text()
            try:
                msg = json.loads(raw)
            except json.JSONDecodeError:
                await websocket.send_json({"type": "error", "detail": "invalid json"})
                continue

            mtype = msg.get("type")
            if mtype == "cursor":
                pos = msg.get("position")
                if isinstance(pos, int) and pos >= 0:
                    manager.set_cursor(document_id, conn_id, pos)
                    await manager.broadcast_json(
                        document_id,
                        {
                            "type": "cursor",
                            "username": username,
                            "position": pos,
                        },
                        exclude_conn_id=conn_id,
                    )
                continue

            if mtype not in (OpType.insert.value, OpType.delete.value):
                await websocket.send_json({"type": "error", "detail": "unknown message type"})
                continue

            base_v = msg.get("base_version")
            if not isinstance(base_v, int) or base_v < 0:
                await websocket.send_json({"type": "error", "detail": "base_version required"})
                continue

            db.refresh(doc)
            server_v = doc.version
            if base_v > server_v:
                await websocket.send_json(
                    {
                        "type": "resync",
                        "content": doc.content,
                        "version": doc.version,
                    }
                )
                continue

            if mtype == OpType.insert.value:
                text = msg.get("text", "")
                pos = msg.get("position")
                if not isinstance(pos, int) or pos < 0 or not isinstance(text, str):
                    await websocket.send_json({"type": "error", "detail": "invalid insert"})
                    continue
                if text == "":
                    await websocket.send_json(
                        {"type": "ack", "version": server_v, "noop": True}
                    )
                    continue
                client_op: InsertOp | DeleteOp = InsertOp(pos, text)
            else:
                pos = msg.get("position")
                length = msg.get("length")
                if (
                    not isinstance(pos, int)
                    or not isinstance(length, int)
                    or pos < 0
                    or length < 1
                ):
                    await websocket.send_json({"type": "error", "detail": "invalid delete"})
                    continue
                client_op = DeleteOp(pos, length)

            pending = (
                db.execute(
                    select(Operation)
                    .where(
                        Operation.document_id == document_id,
                        Operation.version > base_v,
                    )
                    .order_by(Operation.version.asc())
                )
                .scalars()
                .all()
            )

            for row in pending:
                transformed = transform_client_op_against_server_op(client_op, row)
                if transformed is None:
                    client_op = None
                    break
                client_op = transformed

            if client_op is None:
                await websocket.send_json({"type": "ack", "version": server_v, "noop": True})
                continue

            if isinstance(client_op, DeleteOp) and client_op.length <= 0:
                await websocket.send_json({"type": "ack", "version": server_v, "noop": True})
                continue

            db.refresh(doc)
            content = doc.content
            new_content = apply_op_to_string(content, client_op)
            new_version = doc.version + 1

            if isinstance(client_op, InsertOp):
                op_row = Operation(
                    document_id=document_id,
                    user=username,
                    op_type=OpType.insert.value,
                    position=client_op.position,
                    text=client_op.text,
                    delete_length=None,
                    version=new_version,
                )
            else:
                op_row = Operation(
                    document_id=document_id,
                    user=username,
                    op_type=OpType.delete.value,
                    position=client_op.position,
                    text=None,
                    delete_length=client_op.length,
                    version=new_version,
                )

            doc.content = new_content
            doc.version = new_version
            doc.updated_at = datetime.now(timezone.utc)
            db.add(op_row)
            db.commit()
            db.refresh(doc)

            broadcast_op: dict[str, Any] = {
                "type": "op_applied",
                "op_type": op_row.op_type,
                "position": op_row.position,
                "version": new_version,
                "user": username,
            }
            if op_row.op_type == OpType.insert.value:
                broadcast_op["text"] = op_row.text
            else:
                broadcast_op["length"] = op_row.delete_length

            await websocket.send_json(
                {"type": "ack", "version": new_version, "op": broadcast_op}
            )
            await manager.broadcast_json(document_id, broadcast_op, exclude_conn_id=conn_id)

    except WebSocketDisconnect:
        pass
    finally:
        if conn_id:
            manager.disconnect(document_id, conn_id)
        db.close()
        if conn_id:
            try:
                await manager.broadcast_json(
                    document_id,
                    {
                        "type": "user_left",
                        "username": username,
                        "collaborators": [
                            p.model_dump() for p in manager.collaborators(document_id)
                        ],
                    },
                )
            except Exception:
                pass
