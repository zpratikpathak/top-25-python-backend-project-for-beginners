import logging
import os
import random
import threading
import time
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from enum import Enum
from typing import Any, Optional

from fastapi import FastAPI, HTTPException, Query
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import JSON, DateTime, Integer, String, create_engine, func, select, update
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, sessionmaker, Session

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./jobs.db")
WORKER_COUNT = int(os.getenv("WORKER_COUNT", "3"))
WORKER_POLL_INTERVAL_SEC = float(os.getenv("WORKER_POLL_INTERVAL_SEC", "0.25"))
DEFAULT_MAX_RETRIES = 3
BACKOFF_BASE_SEC = 2
BACKOFF_MAX_SEC = 300


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class JobStatus(str, Enum):
    pending = "pending"
    running = "running"
    completed = "completed"
    failed = "failed"
    dead_letter = "dead_letter"
    cancelled = "cancelled"


class Base(DeclarativeBase):
    pass


class Job(Base):
    __tablename__ = "jobs"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    job_type: Mapped[str] = mapped_column(String(128), index=True)
    payload: Mapped[dict[str, Any]] = mapped_column(JSON, default=dict)
    priority: Mapped[int] = mapped_column(Integer, default=5, index=True)
    status: Mapped[str] = mapped_column(String(32), index=True, default=JobStatus.pending.value)
    result: Mapped[Optional[dict[str, Any]]] = mapped_column(JSON, nullable=True)
    error_message: Mapped[Optional[str]] = mapped_column(String(4096), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    max_retries: Mapped[int] = mapped_column(Integer, default=DEFAULT_MAX_RETRIES)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    scheduled_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)


engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False},
    pool_pre_ping=True,
)
SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False)

_stop_event = threading.Event()
_worker_threads: list[threading.Thread] = []


def backoff_seconds(attempt: int) -> int:
    return min(BACKOFF_BASE_SEC ** min(attempt, 8), BACKOFF_MAX_SEC)


def claim_next_job(session: Session) -> Optional[Job]:
    now = utcnow()
    subq = (
        select(Job.id)
        .where(
            Job.status.in_([JobStatus.pending.value, JobStatus.failed.value]),
            (Job.scheduled_at.is_(None)) | (Job.scheduled_at <= now),
        )
        .order_by(Job.priority.desc(), Job.created_at.asc())
        .limit(1)
    )
    jid = session.execute(subq).scalar_one_or_none()
    if not jid:
        return None
    res = session.execute(
        update(Job)
        .where(
            Job.id == jid,
            Job.status.in_([JobStatus.pending.value, JobStatus.failed.value]),
        )
        .values(status=JobStatus.running.value, started_at=now)
    )
    if res.rowcount != 1:
        session.rollback()
        return None
    session.commit()
    return session.get(Job, jid)


def process_job_worker(job_id: str) -> None:
    db = SessionLocal()
    try:
        job = db.get(Job, job_id)
        if not job:
            return
        time.sleep(random.uniform(0.4, 1.6))
        ok = random.random() >= 0.35
        now = utcnow()
        if ok:
            db.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(
                    status=JobStatus.completed.value,
                    result={"ok": True, "message": "simulated success"},
                    error_message=None,
                    completed_at=now,
                )
            )
            db.commit()
            return
        err = "simulated random failure"
        attempts = job.attempts + 1
        if attempts >= job.max_retries:
            db.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(
                    status=JobStatus.dead_letter.value,
                    attempts=attempts,
                    error_message=err,
                    completed_at=now,
                    scheduled_at=None,
                )
            )
        else:
            sched = utcnow() + timedelta(seconds=backoff_seconds(attempts))
            db.execute(
                update(Job)
                .where(Job.id == job_id)
                .values(
                    status=JobStatus.failed.value,
                    attempts=attempts,
                    error_message=err,
                    scheduled_at=sched,
                    started_at=None,
                )
            )
        db.commit()
    except Exception as e:
        logger.exception("worker error for job %s", job_id)
        db.rollback()
        try:
            job = db.get(Job, job_id)
            if not job:
                return
            attempts = job.attempts + 1
            now = utcnow()
            if attempts >= job.max_retries:
                db.execute(
                    update(Job)
                    .where(Job.id == job_id)
                    .values(
                        status=JobStatus.dead_letter.value,
                        attempts=attempts,
                        error_message=str(e)[:4096],
                        completed_at=now,
                        scheduled_at=None,
                    )
                )
            else:
                sched = now + timedelta(seconds=backoff_seconds(attempts))
                db.execute(
                    update(Job)
                    .where(Job.id == job_id)
                    .values(
                        status=JobStatus.failed.value,
                        attempts=attempts,
                        error_message=str(e)[:4096],
                        scheduled_at=sched,
                        started_at=None,
                    )
                )
            db.commit()
        except Exception:
            db.rollback()
    finally:
        db.close()


def worker_loop(worker_id: int) -> None:
    logger.info("worker %s started", worker_id)
    while not _stop_event.is_set():
        db = SessionLocal()
        try:
            job = claim_next_job(db)
        except Exception:
            logger.exception("claim failed")
            db.rollback()
            job = None
        finally:
            db.close()
        if job:
            process_job_worker(job.id)
        else:
            _stop_event.wait(WORKER_POLL_INTERVAL_SEC)
    logger.info("worker %s stopped", worker_id)


def start_workers() -> None:
    _stop_event.clear()
    global _worker_threads
    _worker_threads = []
    for i in range(WORKER_COUNT):
        t = threading.Thread(target=worker_loop, args=(i,), daemon=True, name=f"job-worker-{i}")
        t.start()
        _worker_threads.append(t)


def stop_workers() -> None:
    _stop_event.set()
    for t in _worker_threads:
        t.join(timeout=5.0)


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    start_workers()
    yield
    stop_workers()


app = FastAPI(title="Job Queue API", lifespan=lifespan)


class JobCreate(BaseModel):
    job_type: str = Field(..., min_length=1, max_length=128)
    payload: dict[str, Any] = Field(default_factory=dict)
    priority: int = Field(default=5, ge=1, le=10)
    max_retries: int = Field(default=DEFAULT_MAX_RETRIES, ge=0, le=50)


class JobOut(BaseModel):
    id: str
    job_type: str
    payload: dict[str, Any]
    priority: int
    status: str
    result: Optional[dict[str, Any]]
    error_message: Optional[str]
    attempts: int
    max_retries: int
    created_at: datetime
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    scheduled_at: Optional[datetime]

    model_config = ConfigDict(from_attributes=True)


def job_to_out(j: Job) -> JobOut:
    return JobOut(
        id=j.id,
        job_type=j.job_type,
        payload=j.payload or {},
        priority=j.priority,
        status=j.status,
        result=j.result,
        error_message=j.error_message,
        attempts=j.attempts,
        max_retries=j.max_retries,
        created_at=j.created_at,
        started_at=j.started_at,
        completed_at=j.completed_at,
        scheduled_at=j.scheduled_at,
    )


@app.post("/api/jobs", response_model=JobOut)
def submit_job(body: JobCreate):
    db = SessionLocal()
    try:
        jid = str(uuid.uuid4())
        row = Job(
            id=jid,
            job_type=body.job_type,
            payload=body.payload,
            priority=body.priority,
            status=JobStatus.pending.value,
            max_retries=body.max_retries,
            attempts=0,
        )
        db.add(row)
        db.commit()
        db.refresh(row)
        return job_to_out(row)
    finally:
        db.close()


@app.get("/api/jobs/stats")
def jobs_stats():
    db = SessionLocal()
    try:
        counts = {}
        for s in JobStatus:
            c = db.execute(select(func.count()).select_from(Job).where(Job.status == s.value)).scalar_one()
            counts[s.value] = c
        avg_ms = db.execute(
            select(
                func.avg(
                    (
                        func.julianday(Job.completed_at) - func.julianday(Job.started_at)
                    )
                    * 86400000.0
                )
            ).where(
                Job.status == JobStatus.completed.value,
                Job.started_at.isnot(None),
                Job.completed_at.isnot(None),
            )
        ).scalar_one()
        avg_processing_ms = float(avg_ms) if avg_ms is not None else None
        return {"counts": counts, "avg_processing_time_ms": avg_processing_ms}
    finally:
        db.close()


@app.get("/api/jobs", response_model=dict)
def list_jobs(
    status: Optional[str] = Query(None),
    skip: int = Query(0, ge=0),
    limit: int = Query(50, ge=1, le=200),
):
    db = SessionLocal()
    try:
        cq = select(func.count()).select_from(Job)
        q = select(Job)
        if status:
            if status not in {e.value for e in JobStatus}:
                raise HTTPException(400, "invalid status")
            cq = cq.where(Job.status == status)
            q = q.where(Job.status == status)
        total = db.execute(cq).scalar_one()
        q = q.order_by(Job.created_at.desc()).offset(skip).limit(limit)
        rows = db.execute(q).scalars().all()
        return {
            "total": total,
            "skip": skip,
            "limit": limit,
            "items": [job_to_out(j).model_dump(mode="json") for j in rows],
        }
    finally:
        db.close()


@app.get("/api/jobs/{job_id}", response_model=JobOut)
def get_job(job_id: str):
    db = SessionLocal()
    try:
        j = db.get(Job, job_id)
        if not j:
            raise HTTPException(404, "job not found")
        return job_to_out(j)
    finally:
        db.close()


@app.delete("/api/jobs/{job_id}")
def cancel_job(job_id: str):
    db = SessionLocal()
    try:
        j = db.get(Job, job_id)
        if not j:
            raise HTTPException(404, "job not found")
        if j.status != JobStatus.pending.value:
            raise HTTPException(409, "only pending jobs can be cancelled")
        db.execute(
            update(Job)
            .where(Job.id == job_id, Job.status == JobStatus.pending.value)
            .values(status=JobStatus.cancelled.value)
        )
        db.commit()
        return {"ok": True, "id": job_id, "status": JobStatus.cancelled.value}
    finally:
        db.close()


@app.post("/api/jobs/{job_id}/retry")
def retry_job(job_id: str):
    db = SessionLocal()
    try:
        j = db.get(Job, job_id)
        if not j:
            raise HTTPException(404, "job not found")
        if j.status != JobStatus.failed.value:
            raise HTTPException(409, "only failed jobs can be retried")
        db.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                status=JobStatus.pending.value,
                scheduled_at=None,
                error_message=None,
            )
        )
        db.commit()
        return {"ok": True, "id": job_id, "status": JobStatus.pending.value}
    finally:
        db.close()


@app.get("/api/dead-letter", response_model=dict)
def list_dead_letter(skip: int = Query(0, ge=0), limit: int = Query(50, ge=1, le=200)):
    db = SessionLocal()
    try:
        cq = select(func.count()).select_from(Job).where(Job.status == JobStatus.dead_letter.value)
        total = db.execute(cq).scalar_one()
        rows = (
            db.execute(
                select(Job)
                .where(Job.status == JobStatus.dead_letter.value)
                .order_by(Job.created_at.desc())
                .offset(skip)
                .limit(limit)
            )
            .scalars()
            .all()
        )
        return {
            "total": total,
            "skip": skip,
            "limit": limit,
            "items": [job_to_out(j).model_dump(mode="json") for j in rows],
        }
    finally:
        db.close()


@app.post("/api/dead-letter/{job_id}/requeue")
def requeue_dead_letter(job_id: str):
    db = SessionLocal()
    try:
        j = db.get(Job, job_id)
        if not j:
            raise HTTPException(404, "job not found")
        if j.status != JobStatus.dead_letter.value:
            raise HTTPException(409, "only dead-letter jobs can be requeued")
        db.execute(
            update(Job)
            .where(Job.id == job_id)
            .values(
                status=JobStatus.pending.value,
                attempts=0,
                error_message=None,
                result=None,
                scheduled_at=None,
                started_at=None,
                completed_at=None,
            )
        )
        db.commit()
        return {"ok": True, "id": job_id, "status": JobStatus.pending.value}
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
