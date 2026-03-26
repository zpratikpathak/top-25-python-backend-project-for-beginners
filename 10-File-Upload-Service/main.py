import uuid
from datetime import datetime, timezone
from pathlib import Path

from fastapi import Depends, FastAPI, File, HTTPException, Query, UploadFile
from fastapi.responses import FileResponse
from sqlalchemy import Column, DateTime, Integer, String, create_engine, func
from sqlalchemy.orm import Session, declarative_base, sessionmaker

UPLOAD_DIR = Path(__file__).resolve().parent / "uploads"
MAX_BYTES = 10 * 1024 * 1024
DATABASE_URL = "sqlite:///./files.db"

IMAGE_EXT = {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}
PDF_EXT = {".pdf"}
TEXT_EXT = {".txt", ".md", ".csv"}
ALLOWED_EXT = IMAGE_EXT | PDF_EXT | TEXT_EXT

engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()


class FileRecord(Base):
    __tablename__ = "files"

    id = Column(Integer, primary_key=True, index=True)
    original_filename = Column(String, nullable=False)
    stored_filename = Column(String, nullable=False, unique=True)
    file_size = Column(Integer, nullable=False)
    content_type = Column(String, nullable=True)
    upload_date = Column(DateTime(timezone=True), nullable=False)


def init_db():
    Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def file_category(path: Path) -> str:
    ext = path.suffix.lower()
    if ext in IMAGE_EXT:
        return "image"
    if ext in PDF_EXT:
        return "pdf"
    if ext in TEXT_EXT:
        return "text"
    return "other"


app = FastAPI(title="File Upload Service")


@app.on_event("startup")
def startup():
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    init_db()


@app.post("/api/files/upload")
async def upload_file(
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    if not file.filename:
        raise HTTPException(status_code=400, detail="Filename required")

    suffix = Path(file.filename).suffix.lower()
    if suffix not in ALLOWED_EXT:
        raise HTTPException(
            status_code=400,
            detail=f"File type not allowed. Allowed: {sorted(ALLOWED_EXT)}",
        )

    stored_name = f"{uuid.uuid4().hex}{suffix}"
    dest = UPLOAD_DIR / stored_name

    size = 0
    chunk = 1024 * 1024
    try:
        with open(dest, "wb") as out:
            while True:
                data = await file.read(chunk)
                if not data:
                    break
                size += len(data)
                if size > MAX_BYTES:
                    out.close()
                    dest.unlink(missing_ok=True)
                    raise HTTPException(
                        status_code=413,
                        detail=f"File exceeds maximum size of {MAX_BYTES // (1024 * 1024)}MB",
                    )
                out.write(data)
    except HTTPException:
        raise
    except OSError as e:
        dest.unlink(missing_ok=True)
        raise HTTPException(status_code=500, detail=str(e)) from e

    record = FileRecord(
        original_filename=file.filename,
        stored_filename=stored_name,
        file_size=size,
        content_type=file.content_type,
        upload_date=datetime.now(timezone.utc),
    )
    db.add(record)
    db.commit()
    db.refresh(record)
    return {
        "id": record.id,
        "original_filename": record.original_filename,
        "stored_filename": record.stored_filename,
        "file_size": record.file_size,
        "content_type": record.content_type,
        "upload_date": record.upload_date.isoformat(),
    }


@app.get("/api/files/stats")
def get_stats(db: Session = Depends(get_db)):
    total_files = db.query(func.count(FileRecord.id)).scalar() or 0
    total_size = db.query(func.coalesce(func.sum(FileRecord.file_size), 0)).scalar()
    rows = db.query(FileRecord.stored_filename).all()
    by_type = {"image": 0, "pdf": 0, "text": 0}
    for (stored,) in rows:
        cat = file_category(Path(stored))
        if cat in by_type:
            by_type[cat] += 1
    return {
        "total_files": total_files,
        "total_size_bytes": int(total_size),
        "files_by_type": by_type,
    }


@app.get("/api/files")
def list_files(
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    db: Session = Depends(get_db),
):
    q = db.query(FileRecord).order_by(FileRecord.upload_date.desc())
    total = q.count()
    items = q.offset(skip).limit(limit).all()
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "items": [
            {
                "id": r.id,
                "original_filename": r.original_filename,
                "stored_filename": r.stored_filename,
                "file_size": r.file_size,
                "content_type": r.content_type,
                "upload_date": r.upload_date.isoformat(),
            }
            for r in items
        ],
    }


@app.get("/api/files/{file_id}")
def get_metadata(file_id: int, db: Session = Depends(get_db)):
    r = db.query(FileRecord).filter(FileRecord.id == file_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="File not found")
    return {
        "id": r.id,
        "original_filename": r.original_filename,
        "stored_filename": r.stored_filename,
        "file_size": r.file_size,
        "content_type": r.content_type,
        "upload_date": r.upload_date.isoformat(),
    }


@app.get("/api/files/{file_id}/download")
def download_file(file_id: int, db: Session = Depends(get_db)):
    r = db.query(FileRecord).filter(FileRecord.id == file_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="File not found")
    path = UPLOAD_DIR / r.stored_filename
    if not path.is_file():
        raise HTTPException(status_code=404, detail="File missing on disk")
    media = r.content_type or "application/octet-stream"
    return FileResponse(
        path=path,
        filename=r.original_filename,
        media_type=media,
    )


@app.delete("/api/files/{file_id}")
def delete_file(file_id: int, db: Session = Depends(get_db)):
    r = db.query(FileRecord).filter(FileRecord.id == file_id).first()
    if not r:
        raise HTTPException(status_code=404, detail="File not found")
    path = UPLOAD_DIR / r.stored_filename
    if path.is_file():
        try:
            path.unlink()
        except OSError as e:
            raise HTTPException(status_code=500, detail=str(e)) from e
    db.delete(r)
    db.commit()
    return {"deleted": True, "id": file_id}
