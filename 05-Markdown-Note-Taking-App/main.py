from datetime import datetime, timezone
from typing import Optional

import markdown
from fastapi import Depends, FastAPI, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field
from sqlalchemy import String, Text, DateTime, Integer, create_engine, func, or_, select
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session

DATABASE_URL = "sqlite:///./notes.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Note(Base):
    __tablename__ = "notes"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, onupdate=utc_now
    )


Base.metadata.create_all(bind=engine)

app = FastAPI(title="Markdown Note API", version="1.0.0")


def render_md(text: str) -> str:
    return markdown.markdown(text, extensions=["fenced_code", "tables", "nl2br"])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class NoteCreate(BaseModel):
    title: str = Field(..., min_length=1, max_length=500)
    content: str = Field(default="")


class NoteUpdate(BaseModel):
    title: Optional[str] = Field(None, min_length=1, max_length=500)
    content: Optional[str] = None


class NoteSummary(BaseModel):
    id: int
    title: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class NoteDetail(BaseModel):
    id: int
    title: str
    content: str
    html: str
    created_at: datetime
    updated_at: datetime

    model_config = {"from_attributes": True}


class NoteListResponse(BaseModel):
    items: list[NoteSummary]
    page: int
    per_page: int
    total: int


@app.post("/api/notes", response_model=NoteDetail, status_code=201)
def create_note(payload: NoteCreate, db: Session = Depends(get_db)):
    note = Note(title=payload.title.strip(), content=payload.content)
    db.add(note)
    db.commit()
    db.refresh(note)
    return NoteDetail(
        id=note.id,
        title=note.title,
        content=note.content,
        html=render_md(note.content),
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


@app.get("/api/notes", response_model=NoteListResponse)
def list_notes(
    db: Session = Depends(get_db),
    search: Optional[str] = Query(None, description="Filter by title or markdown content"),
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=100),
):
    stmt = select(Note)
    count_stmt = select(func.count()).select_from(Note)
    if search and search.strip():
        term = f"%{search.strip()}%"
        filt = or_(Note.title.ilike(term), Note.content.ilike(term))
        stmt = stmt.where(filt)
        count_stmt = count_stmt.where(filt)
    total = db.scalar(count_stmt) or 0
    stmt = stmt.order_by(Note.updated_at.desc()).offset((page - 1) * per_page).limit(per_page)
    rows = db.scalars(stmt).all()
    return NoteListResponse(
        items=[NoteSummary.model_validate(n) for n in rows],
        page=page,
        per_page=per_page,
        total=total,
    )


@app.get("/api/notes/{note_id}", response_model=NoteDetail)
def get_note(note_id: int, db: Session = Depends(get_db)):
    note = db.get(Note, note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    return NoteDetail(
        id=note.id,
        title=note.title,
        content=note.content,
        html=render_md(note.content),
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


@app.put("/api/notes/{note_id}", response_model=NoteDetail)
def update_note(note_id: int, payload: NoteUpdate, db: Session = Depends(get_db)):
    note = db.get(Note, note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    if payload.title is not None:
        note.title = payload.title.strip()
    if payload.content is not None:
        note.content = payload.content
    if payload.title is None and payload.content is None:
        raise HTTPException(status_code=400, detail="No fields to update")
    note.updated_at = utc_now()
    db.commit()
    db.refresh(note)
    return NoteDetail(
        id=note.id,
        title=note.title,
        content=note.content,
        html=render_md(note.content),
        created_at=note.created_at,
        updated_at=note.updated_at,
    )


@app.delete("/api/notes/{note_id}", status_code=204)
def delete_note(note_id: int, db: Session = Depends(get_db)):
    note = db.get(Note, note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    db.delete(note)
    db.commit()
    return None


@app.get("/api/notes/{note_id}/html", response_class=HTMLResponse)
def get_note_html(note_id: int, db: Session = Depends(get_db)):
    note = db.get(Note, note_id)
    if not note:
        raise HTTPException(status_code=404, detail="Note not found")
    return render_md(note.content)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
