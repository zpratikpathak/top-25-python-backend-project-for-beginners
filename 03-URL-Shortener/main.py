import hashlib
import secrets
import string
from datetime import datetime, timezone

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import RedirectResponse
from pydantic import BaseModel, ConfigDict, HttpUrl
from sqlalchemy import create_engine, Integer, String, DateTime
from sqlalchemy.orm import Session, declarative_base, sessionmaker, Mapped, mapped_column

DATABASE_URL = "sqlite:///./urls.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
Base = declarative_base()

BASE62 = string.ascii_letters + string.digits


class ShortenedUrl(Base):
    __tablename__ = "shortened_urls"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    original_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    short_code: Mapped[str] = mapped_column(String(32), unique=True, index=True, nullable=False)
    click_count: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        insert_default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )


Base.metadata.create_all(bind=engine)

app = FastAPI(title="URL Shortener")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _to_base62(n: int, length: int = 8) -> str:
    base = 62**length
    n = n % base
    if n == 0:
        return BASE62[0] * length
    digits = []
    while n:
        n, r = divmod(n, 62)
        digits.append(BASE62[r])
    s = "".join(reversed(digits))
    return s.rjust(length, BASE62[0])[-length:]


def generate_short_code(original_url: str) -> str:
    digest = hashlib.sha256(
        f"{original_url}:{secrets.token_urlsafe(16)}".encode()
    ).digest()
    n = int.from_bytes(digest[:12], "big")
    return _to_base62(n, 8)


class ShortenRequest(BaseModel):
    url: HttpUrl


class ShortenResponse(BaseModel):
    short_code: str
    short_url: str


class UrlStats(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    original_url: str
    short_code: str
    click_count: int
    created_at: datetime


@app.post("/api/shorten", response_model=ShortenResponse)
def shorten(body: ShortenRequest, db: Session = Depends(get_db)):
    url_str = str(body.url)
    for _ in range(20):
        code = generate_short_code(url_str)
        if db.query(ShortenedUrl).filter(ShortenedUrl.short_code == code).first():
            continue
        row = ShortenedUrl(original_url=url_str, short_code=code, click_count=0)
        db.add(row)
        db.commit()
        db.refresh(row)
        return ShortenResponse(short_code=code, short_url=f"/{code}")
    raise HTTPException(status_code=500, detail="Could not allocate short code")


@app.get("/api/urls/{short_code}/stats", response_model=UrlStats)
def get_stats(short_code: str, db: Session = Depends(get_db)):
    row = db.query(ShortenedUrl).filter(ShortenedUrl.short_code == short_code).first()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    return row


@app.get("/api/urls", response_model=list[UrlStats])
def list_urls(db: Session = Depends(get_db)):
    rows = db.query(ShortenedUrl).order_by(ShortenedUrl.created_at.desc()).all()
    return rows


@app.delete("/api/urls/{short_code}", status_code=204)
def delete_url(short_code: str, db: Session = Depends(get_db)):
    row = db.query(ShortenedUrl).filter(ShortenedUrl.short_code == short_code).first()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(row)
    db.commit()


@app.get("/{short_code}")
def redirect(short_code: str, db: Session = Depends(get_db)):
    row = db.query(ShortenedUrl).filter(ShortenedUrl.short_code == short_code).first()
    if not row:
        raise HTTPException(status_code=404, detail="Not found")
    row.click_count += 1
    db.commit()
    return RedirectResponse(url=row.original_url, status_code=307)
