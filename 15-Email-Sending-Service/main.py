import asyncio
import logging
import os
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from typing import Any, Optional

import aiosmtplib
from dotenv import load_dotenv
from fastapi import BackgroundTasks, Depends, FastAPI, HTTPException, Query
from jinja2 import Environment, StrictUndefined, TemplateError
from pydantic import BaseModel, EmailStr, Field
from sqlalchemy import String, Text, create_engine, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

load_dotenv()

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = "sqlite:///./emails.db"
engine = create_engine(
    DATABASE_URL, connect_args={"check_same_thread": False}
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)
jinja_env = Environment(undefined=StrictUndefined, autoescape=False)
preview_jinja_env = Environment(autoescape=False)


class Base(DeclarativeBase):
    pass


class Email(Base):
    __tablename__ = "emails"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    to_address: Mapped[str] = mapped_column(String(512), index=True)
    subject: Mapped[str] = mapped_column(String(998))
    body: Mapped[str] = mapped_column(Text())
    html_body: Mapped[Optional[str]] = mapped_column(Text(), nullable=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default="pending")
    error_message: Mapped[Optional[str]] = mapped_column(Text(), nullable=True)
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    sent_at: Mapped[Optional[datetime]] = mapped_column(nullable=True)


class Template(Base):
    __tablename__ = "templates"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, index=True)
    subject_template: Mapped[str] = mapped_column(String(998))
    body_template: Mapped[str] = mapped_column(Text())
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))


def get_smtp_config() -> dict[str, Any]:
    return {
        "host": os.getenv("SMTP_HOST", "").strip(),
        "port": int(os.getenv("SMTP_PORT", "587")),
        "user": os.getenv("SMTP_USER", "").strip(),
        "password": os.getenv("SMTP_PASSWORD", "").strip(),
        "from_addr": os.getenv("SMTP_FROM", "").strip(),
        "use_tls": os.getenv("SMTP_USE_TLS", "true").lower() in ("1", "true", "yes"),
        "mock": os.getenv("SMTP_MOCK", "").lower() in ("1", "true", "yes")
        or not os.getenv("SMTP_HOST", "").strip(),
    }


def get_db() -> Session:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


app = FastAPI(title="Email Sending Service", version="1.0.0")


@app.on_event("startup")
def on_startup() -> None:
    Base.metadata.create_all(bind=engine)


class SendEmailRequest(BaseModel):
    to: EmailStr
    subject: str = Field(..., min_length=1, max_length=998)
    body: str = Field(..., min_length=1)
    html_body: Optional[str] = None


class SendTemplateRequest(BaseModel):
    to: EmailStr
    template_name: str = Field(..., min_length=1, max_length=255)
    variables: dict[str, Any] = Field(default_factory=dict)


class TemplateCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    subject_template: str = Field(..., min_length=1, max_length=998)
    body_template: str = Field(..., min_length=1)


class EmailOut(BaseModel):
    id: int
    to_address: str
    subject: str
    body: str
    html_body: Optional[str]
    status: str
    error_message: Optional[str]
    created_at: datetime
    sent_at: Optional[datetime]

    model_config = {"from_attributes": True}


class TemplateOut(BaseModel):
    id: int
    name: str
    subject_template: str
    body_template: str
    created_at: datetime

    model_config = {"from_attributes": True}


class TemplateDetailOut(TemplateOut):
    preview_subject: str
    preview_body: str


def render_template_strings(
    subject_t: str, body_t: str, variables: dict[str, Any]
) -> tuple[str, str]:
    try:
        subj = jinja_env.from_string(subject_t).render(**variables)
        body = jinja_env.from_string(body_t).render(**variables)
    except TemplateError as e:
        raise HTTPException(status_code=400, detail=f"Template error: {e}") from e
    return subj, body


async def deliver_email(email_id: int) -> None:
    cfg = get_smtp_config()
    db = SessionLocal()
    try:
        row = db.get(Email, email_id)
        if not row:
            return
        if row.status != "pending":
            return

        if cfg["mock"]:
            await asyncio.sleep(0.3)
            logger.info(
                "[MOCK] Would send email id=%s to=%s subject=%s",
                email_id,
                row.to_address,
                row.subject[:80],
            )
            row.status = "sent"
            row.sent_at = datetime.now(timezone.utc)
            row.error_message = None
            db.commit()
            return

        from_addr = cfg["from_addr"] or cfg["user"]
        if not from_addr:
            row.status = "failed"
            row.error_message = "SMTP_FROM or SMTP_USER must be set for real SMTP"
            db.commit()
            return

        msg = MIMEMultipart("alternative")
        msg["Subject"] = row.subject
        msg["From"] = from_addr
        msg["To"] = row.to_address
        msg.attach(MIMEText(row.body, "plain", "utf-8"))
        if row.html_body:
            msg.attach(MIMEText(row.html_body, "html", "utf-8"))

        try:
            await aiosmtplib.send(
                msg,
                hostname=cfg["host"],
                port=cfg["port"],
                username=cfg["user"] or None,
                password=cfg["password"] or None,
                start_tls=cfg["use_tls"],
            )
            row.status = "sent"
            row.sent_at = datetime.now(timezone.utc)
            row.error_message = None
        except Exception as e:
            logger.exception("SMTP failed for email id=%s", email_id)
            row.status = "failed"
            row.error_message = str(e)[:2000]
        db.commit()
    finally:
        db.close()


@app.post("/api/emails/send", response_model=EmailOut)
async def send_email(
    payload: SendEmailRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> Email:
    rec = Email(
        to_address=payload.to,
        subject=payload.subject,
        body=payload.body,
        html_body=payload.html_body,
        status="pending",
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    background_tasks.add_task(deliver_email, rec.id)
    return rec


@app.post("/api/emails/send-template", response_model=EmailOut)
async def send_template_email(
    payload: SendTemplateRequest,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
) -> Email:
    tpl = db.scalar(select(Template).where(Template.name == payload.template_name))
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")
    subject, body = render_template_strings(
        tpl.subject_template, tpl.body_template, payload.variables
    )
    rec = Email(
        to_address=payload.to,
        subject=subject,
        body=body,
        html_body=None,
        status="pending",
    )
    db.add(rec)
    db.commit()
    db.refresh(rec)
    background_tasks.add_task(deliver_email, rec.id)
    return rec


@app.get("/api/emails", response_model=list[EmailOut])
def list_emails(
    db: Session = Depends(get_db),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
    status: Optional[str] = Query(
        None, pattern="^(pending|sent|failed)$"
    ),
) -> list[Email]:
    q = select(Email).order_by(Email.created_at.desc()).offset(skip).limit(limit)
    if status:
        q = q.where(Email.status == status)
    return list(db.scalars(q).all())


@app.get("/api/emails/{email_id}", response_model=EmailOut)
def get_email(email_id: int, db: Session = Depends(get_db)) -> Email:
    row = db.get(Email, email_id)
    if not row:
        raise HTTPException(status_code=404, detail="Email not found")
    return row


@app.post("/api/templates", response_model=TemplateOut, status_code=201)
def create_template(payload: TemplateCreate, db: Session = Depends(get_db)) -> Template:
    existing = db.scalar(select(Template).where(Template.name == payload.name))
    if existing:
        raise HTTPException(status_code=409, detail="Template name already exists")
    try:
        jinja_env.parse(payload.subject_template)
        jinja_env.parse(payload.body_template)
    except TemplateError as e:
        raise HTTPException(status_code=400, detail=f"Invalid Jinja2: {e}") from e
    tpl = Template(
        name=payload.name,
        subject_template=payload.subject_template,
        body_template=payload.body_template,
    )
    db.add(tpl)
    db.commit()
    db.refresh(tpl)
    return tpl


@app.get("/api/templates", response_model=list[TemplateOut])
def list_templates(db: Session = Depends(get_db)) -> list[Template]:
    rows = db.scalars(select(Template).order_by(Template.name)).all()
    return list(rows)


@app.get("/api/templates/{name}", response_model=TemplateDetailOut)
def get_template(name: str, db: Session = Depends(get_db)) -> TemplateDetailOut:
    tpl = db.scalar(select(Template).where(Template.name == name))
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")
    try:
        preview_subj = preview_jinja_env.from_string(tpl.subject_template).render()
        preview_body = preview_jinja_env.from_string(tpl.body_template).render()
    except TemplateError as e:
        raise HTTPException(status_code=400, detail=f"Preview render error: {e}") from e
    return TemplateDetailOut(
        id=tpl.id,
        name=tpl.name,
        subject_template=tpl.subject_template,
        body_template=tpl.body_template,
        created_at=tpl.created_at,
        preview_subject=preview_subj,
        preview_body=preview_body,
    )


@app.delete("/api/templates/{name}", status_code=204)
def delete_template(name: str, db: Session = Depends(get_db)) -> None:
    tpl = db.scalar(select(Template).where(Template.name == name))
    if not tpl:
        raise HTTPException(status_code=404, detail="Template not found")
    db.delete(tpl)
    db.commit()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
