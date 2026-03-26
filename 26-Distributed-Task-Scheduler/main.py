from __future__ import annotations

import asyncio
import logging
import threading
import os
import socket
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from typing import Any, Generator, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    func,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

DATABASE_URL = os.getenv("DATABASE_URL", "sqlite:///./scheduler.db")
engine = create_engine(
    DATABASE_URL,
    connect_args={"check_same_thread": False} if DATABASE_URL.startswith("sqlite") else {},
)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

WORKER_ID = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
LOCK_TTL_SEC = 300
SCHEDULER_INTERVAL_SEC = 10
BACKOFF_BASE_SEC = 30
BACKOFF_MAX_SEC = 3600


def utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Base(DeclarativeBase):
    pass


class Task(Base):
    __tablename__ = "tasks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    task_type: Mapped[str] = mapped_column(String(64), nullable=False)
    cron_expression: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    payload: Mapped[dict] = mapped_column(JSON, default=dict)
    enabled: Mapped[bool] = mapped_column(Boolean, default=True)
    max_retries: Mapped[int] = mapped_column(Integer, default=3)
    next_run_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    dependencies: Mapped[list["TaskDependency"]] = relationship(
        "TaskDependency",
        foreign_keys="TaskDependency.task_id",
        back_populates="task",
        cascade="all, delete-orphan",
    )
    executions: Mapped[list["Execution"]] = relationship("Execution", back_populates="task")


class TaskDependency(Base):
    __tablename__ = "task_dependencies"
    __table_args__ = (UniqueConstraint("task_id", "depends_on_id", name="uq_task_dep"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    depends_on_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)

    task: Mapped["Task"] = relationship(
        "Task",
        foreign_keys=[task_id],
        back_populates="dependencies",
    )


class DagExecution(Base):
    __tablename__ = "dag_executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    status: Mapped[str] = mapped_column(String(32), default="pending")
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)

    executions: Mapped[list["Execution"]] = relationship("Execution", back_populates="dag_execution")


class Execution(Base):
    __tablename__ = "executions"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    dag_execution_id: Mapped[Optional[int]] = mapped_column(
        ForeignKey("dag_executions.id", ondelete="SET NULL"), nullable=True
    )
    status: Mapped[str] = mapped_column(String(32), default="pending")
    result: Mapped[Optional[dict]] = mapped_column(JSON, nullable=True)
    error: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    started_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    completed_at: Mapped[Optional[datetime]] = mapped_column(DateTime(timezone=True), nullable=True)
    attempt: Mapped[int] = mapped_column(Integer, default=1)

    task: Mapped["Task"] = relationship("Task", back_populates="executions")
    dag_execution: Mapped[Optional["DagExecution"]] = relationship(
        "DagExecution", back_populates="executions"
    )


class TaskLock(Base):
    __tablename__ = "task_locks"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    task_id: Mapped[int] = mapped_column(ForeignKey("tasks.id", ondelete="CASCADE"), unique=True, nullable=False)
    locked_by: Mapped[str] = mapped_column(String(255), nullable=False)
    locked_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


def init_db() -> None:
    Base.metadata.create_all(bind=engine)


def get_db() -> Generator[Session, None, None]:
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class CronParser:
    def __init__(self, expression: str) -> None:
        parts = expression.strip().split()
        if len(parts) != 5:
            raise ValueError("cron must have 5 fields: minute hour day month weekday")
        self.minute_f, self.hour_f, self.dom_f, self.month_f, self.dow_f = parts

    @staticmethod
    def _expand_field(field: str, lo: int, hi: int) -> Optional[set[int]]:
        if field == "*":
            return None
        out: set[int] = set()
        for part in field.split(","):
            part = part.strip()
            if "/" in part:
                base, step_s = part.split("/", 1)
                step = int(step_s)
                if base == "*":
                    vals = list(range(lo, hi + 1))
                elif "-" in base:
                    a, b = base.split("-", 1)
                    vals = list(range(int(a), int(b) + 1))
                else:
                    vals = [int(base)]
                for v in vals[::step]:
                    if lo <= v <= hi:
                        out.add(v)
            elif "-" in part:
                a, b = part.split("-", 1)
                for v in range(int(a), int(b) + 1):
                    if lo <= v <= hi:
                        out.add(v)
            else:
                v = int(part)
                if lo <= v <= hi:
                    out.add(v)
        return out

    def _matches(self, dt: datetime) -> bool:
        minute = dt.minute
        hour = dt.hour
        dom = dt.day
        month = dt.month
        py_wd = dt.weekday()
        cron_wd = (py_wd + 1) % 7

        m = self._expand_field(self.minute_f, 0, 59)
        h = self._expand_field(self.hour_f, 0, 23)
        d = self._expand_field(self.dom_f, 1, 31)
        mo = self._expand_field(self.month_f, 1, 12)
        dow = self._expand_field(self.dow_f, 0, 7)

        if m is not None and minute not in m:
            return False
        if h is not None and hour not in h:
            return False
        if mo is not None and month not in mo:
            return False

        dom_ok = True
        if d is not None and dom not in d:
            dom_ok = False
        dow_ok = True
        if dow is not None:
            wd_set = set(dow)
            if 7 in wd_set:
                wd_set.add(0)
            if cron_wd not in wd_set:
                dow_ok = False

        if self.dom_f == "*" and self.dow_f != "*":
            return dow_ok
        if self.dow_f == "*" and self.dom_f != "*":
            return dom_ok
        if self.dom_f == "*" and self.dow_f == "*":
            return True
        return dom_ok or dow_ok

    def next_after(self, after: datetime) -> datetime:
        if after.tzinfo is None:
            after = after.replace(tzinfo=timezone.utc)
        cur = after.replace(second=0, microsecond=0) + timedelta(minutes=1)
        cap = after + timedelta(days=366 * 2)
        while cur <= cap:
            if self._matches(cur):
                return cur
            cur += timedelta(minutes=1)
        raise ValueError("no next run within search window")


def compute_next_run(task: Task) -> Optional[datetime]:
    now = utcnow()
    if task.cron_expression:
        try:
            return CronParser(task.cron_expression).next_after(now - timedelta(minutes=1))
        except ValueError:
            return None
    if task.run_at:
        if task.run_at.tzinfo is None:
            ra = task.run_at.replace(tzinfo=timezone.utc)
        else:
            ra = task.run_at
        if ra > now:
            return ra
    return None


def backoff_seconds(attempt: int) -> int:
    return min(BACKOFF_BASE_SEC * (2 ** max(0, attempt - 1)), BACKOFF_MAX_SEC)


def path_exists(db: Session, start_task_id: int, end_task_id: int) -> bool:
    if start_task_id == end_task_id:
        return True
    stack = [start_task_id]
    seen = {start_task_id}
    while stack:
        u = stack.pop()
        rows = db.execute(
            select(TaskDependency.task_id).where(TaskDependency.depends_on_id == u)
        ).all()
        for (tid,) in rows:
            if tid == end_task_id:
                return True
            if tid not in seen:
                seen.add(tid)
                stack.append(tid)
    return False


def dependencies_satisfied(db: Session, task_id: int) -> bool:
    deps = db.execute(
        select(TaskDependency.depends_on_id).where(TaskDependency.task_id == task_id)
    ).scalars().all()
    if not deps:
        return True
    for dep_id in deps:
        ex = (
            db.execute(
                select(Execution)
                .where(Execution.task_id == dep_id)
                .order_by(Execution.started_at.desc())
                .limit(1)
            )
            .scalars()
            .first()
        )
        if ex is None or ex.status != "success":
            return False
    return True


def release_expired_locks(db: Session) -> None:
    now = utcnow()
    db.query(TaskLock).filter(TaskLock.expires_at < now).delete()
    db.commit()


def acquire_task_lock(db: Session, task_id: int) -> bool:
    release_expired_locks(db)
    now = utcnow()
    exp = now + timedelta(seconds=LOCK_TTL_SEC)
    lock = TaskLock(task_id=task_id, locked_by=WORKER_ID, locked_at=now, expires_at=exp)
    db.add(lock)
    try:
        db.commit()
        return True
    except IntegrityError:
        db.rollback()
        return False


def release_task_lock(db: Session, task_id: int) -> None:
    db.query(TaskLock).filter(TaskLock.task_id == task_id, TaskLock.locked_by == WORKER_ID).delete()
    db.commit()


def latest_execution(db: Session, task_id: int) -> Optional[Execution]:
    return (
        db.execute(
            select(Execution)
            .where(Execution.task_id == task_id)
            .order_by(Execution.id.desc())
            .limit(1)
        )
        .scalars()
        .first()
    )


def next_attempt_number(db: Session, task_id: int) -> int:
    last = latest_execution(db, task_id)
    if last is None:
        return 1
    if last.status == "success":
        return 1
    task = db.get(Task, task_id)
    max_r = task.max_retries if task else 3
    if last.attempt >= max_r:
        return 1
    return last.attempt + 1


def run_task_handler(task_type: str, payload: dict[str, Any]) -> dict[str, Any]:
    if task_type == "noop":
        return {"ok": True, "message": "noop"}
    if task_type == "echo":
        return {"echo": payload}
    if task_type == "fail":
        raise RuntimeError(payload.get("message", "planned failure"))
    return {"handled": task_type, "payload": payload}


def execute_task_core(db: Session, task: Task, dag_execution_id: Optional[int], attempt: int) -> Execution:
    ex = Execution(
        task_id=task.id,
        dag_execution_id=dag_execution_id,
        status="running",
        started_at=utcnow(),
        attempt=attempt,
    )
    db.add(ex)
    db.commit()
    db.refresh(ex)
    try:
        result = run_task_handler(task.task_type, task.payload or {})
        ex.status = "success"
        ex.result = result
        ex.error = None
    except Exception as e:
        ex.status = "failed"
        ex.error = str(e)
        ex.result = None
    ex.completed_at = utcnow()
    db.commit()
    db.refresh(ex)
    return ex


def schedule_after_result(db: Session, task: Task, ex: Execution) -> None:
    db.refresh(task)
    if ex.status == "success":
        if task.cron_expression:
            try:
                task.next_run_at = CronParser(task.cron_expression).next_after(ex.completed_at or utcnow())
            except ValueError:
                task.next_run_at = None
        elif task.run_at:
            task.next_run_at = None
            task.enabled = False
        else:
            task.next_run_at = None
    else:
        if ex.attempt < task.max_retries:
            task.next_run_at = utcnow() + timedelta(seconds=backoff_seconds(ex.attempt))
        else:
            task.next_run_at = compute_next_run(task)
    db.commit()


def process_due_tasks() -> None:
    db = SessionLocal()
    try:
        now = utcnow()
        stmt = (
            select(Task)
            .where(Task.enabled.is_(True))
            .where(Task.next_run_at.isnot(None))
            .where(Task.next_run_at <= now)
            .order_by(Task.next_run_at.asc())
        )
        tasks = db.execute(stmt).scalars().all()
        for task in tasks:
            if not acquire_task_lock(db, task.id):
                continue
            try:
                if not dependencies_satisfied(db, task.id):
                    continue
                attempt = next_attempt_number(db, task.id)
                ex = execute_task_core(db, task, None, attempt)
                schedule_after_result(db, task, ex)
            finally:
                release_task_lock(db, task.id)
    except Exception:
        logger.exception("scheduler tick failed")
    finally:
        db.close()


_scheduler_thread: Optional[threading.Thread] = None
_scheduler_running = False


def _scheduler_thread_main() -> None:
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)

    async def run() -> None:
        while _scheduler_running:
            await asyncio.to_thread(process_due_tasks)
            await asyncio.sleep(SCHEDULER_INTERVAL_SEC)

    try:
        loop.run_until_complete(run())
    finally:
        loop.close()


def start_scheduler_bg() -> None:
    global _scheduler_thread, _scheduler_running
    if _scheduler_running:
        return
    _scheduler_running = True
    _scheduler_thread = threading.Thread(target=_scheduler_thread_main, daemon=True, name="task-scheduler")
    _scheduler_thread.start()


def stop_scheduler_bg() -> None:
    global _scheduler_thread, _scheduler_running
    _scheduler_running = False
    if _scheduler_thread is not None:
        _scheduler_thread.join(timeout=SCHEDULER_INTERVAL_SEC + 3)
    _scheduler_thread = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    init_db()
    yield
    stop_scheduler_bg()
    engine.dispose()


app = FastAPI(title="Distributed Task Scheduler", lifespan=lifespan)


class TaskCreate(BaseModel):
    name: str
    task_type: str
    cron_expression: Optional[str] = None
    run_at: Optional[datetime] = None
    payload: dict[str, Any] = Field(default_factory=dict)
    enabled: bool = True
    max_retries: int = 3

    @field_validator("run_at")
    @classmethod
    def normalize_run_at(cls, v: Optional[datetime]) -> Optional[datetime]:
        if v is None:
            return None
        if v.tzinfo is None:
            return v.replace(tzinfo=timezone.utc)
        return v


class TaskUpdate(BaseModel):
    name: Optional[str] = None
    task_type: Optional[str] = None
    cron_expression: Optional[str] = None
    run_at: Optional[datetime] = None
    payload: Optional[dict[str, Any]] = None
    enabled: Optional[bool] = None
    max_retries: Optional[int] = None


class TaskOut(BaseModel):
    id: int
    name: str
    task_type: str
    cron_expression: Optional[str]
    run_at: Optional[datetime]
    payload: dict
    enabled: bool
    max_retries: int
    next_run_at: Optional[datetime]
    created_at: datetime

    class Config:
        from_attributes = True


class ExecutionOut(BaseModel):
    id: int
    task_id: int
    dag_execution_id: Optional[int]
    status: str
    result: Optional[dict]
    error: Optional[str]
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    attempt: int

    class Config:
        from_attributes = True


class TaskDetailOut(TaskOut):
    executions: list[ExecutionOut] = Field(default_factory=list)


class DependencyCreate(BaseModel):
    depends_on_task_id: int


class DependencyOut(BaseModel):
    id: int
    task_id: int
    depends_on_id: int

    class Config:
        from_attributes = True


class DagExecuteBody(BaseModel):
    task_ids: list[int]


class DagStatusOut(BaseModel):
    id: int
    status: str
    started_at: Optional[datetime]
    completed_at: Optional[datetime]
    executions: list[ExecutionOut]


class SchedulerStatusOut(BaseModel):
    running: bool
    worker_id: str
    interval_seconds: int
    next_poll_in_seconds: Optional[float]
    upcoming_tasks: list[TaskOut]


def task_to_out(t: Task) -> TaskOut:
    return TaskOut.model_validate(t)


@app.post("/api/tasks", response_model=TaskOut)
def create_task(body: TaskCreate, db: Session = Depends(get_db)):
    t = Task(
        name=body.name,
        task_type=body.task_type,
        cron_expression=body.cron_expression,
        run_at=body.run_at,
        payload=body.payload,
        enabled=body.enabled,
        max_retries=body.max_retries,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    if body.cron_expression:
        try:
            t.next_run_at = CronParser(body.cron_expression).next_after(utcnow() - timedelta(minutes=1))
        except ValueError as e:
            raise HTTPException(400, f"invalid cron: {e}") from e
    elif body.run_at:
        t.next_run_at = body.run_at if body.run_at > utcnow() else None
    db.commit()
    db.refresh(t)
    return task_to_out(t)


@app.get("/api/tasks", response_model=dict)
def list_tasks(
    db: Session = Depends(get_db),
    enabled: Optional[bool] = None,
    task_type: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
):
    q = select(Task)
    cq = select(func.count()).select_from(Task)
    if enabled is not None:
        q = q.where(Task.enabled == enabled)
        cq = cq.where(Task.enabled == enabled)
    if task_type:
        q = q.where(Task.task_type == task_type)
        cq = cq.where(Task.task_type == task_type)
    total = db.execute(cq).scalar_one()
    rows = db.execute(q.order_by(Task.id.desc()).offset(skip).limit(limit)).scalars().all()
    return {"total": total, "items": [task_to_out(t) for t in rows]}


@app.get("/api/tasks/{task_id}", response_model=TaskDetailOut)
def get_task(task_id: int, db: Session = Depends(get_db), history_limit: int = Query(50, ge=1, le=200)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    hist = (
        db.execute(
            select(Execution)
            .where(Execution.task_id == task_id)
            .order_by(Execution.id.desc())
            .limit(history_limit)
        )
        .scalars()
        .all()
    )
    base = task_to_out(t).model_dump()
    base["executions"] = [ExecutionOut.model_validate(e) for e in hist]
    return TaskDetailOut(**base)


@app.put("/api/tasks/{task_id}", response_model=TaskOut)
def update_task(task_id: int, body: TaskUpdate, db: Session = Depends(get_db)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    data = body.model_dump(exclude_unset=True)
    if "run_at" in data and data["run_at"] is not None:
        ra = data["run_at"]
        if ra.tzinfo is None:
            data["run_at"] = ra.replace(tzinfo=timezone.utc)
    for k, v in data.items():
        setattr(t, k, v)
    db.commit()
    if "cron_expression" in data or "run_at" in data or "enabled" in data:
        if t.cron_expression:
            try:
                t.next_run_at = CronParser(t.cron_expression).next_after(utcnow() - timedelta(minutes=1))
            except ValueError as e:
                raise HTTPException(400, f"invalid cron: {e}") from e
        elif t.run_at:
            t.next_run_at = t.run_at if t.run_at > utcnow() else None
        else:
            t.next_run_at = None
        db.commit()
    db.refresh(t)
    return task_to_out(t)


@app.delete("/api/tasks/{task_id}")
def delete_task(task_id: int, db: Session = Depends(get_db)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    db.delete(t)
    db.commit()
    return {"deleted": True, "id": task_id}


@app.post("/api/tasks/{task_id}/trigger", response_model=ExecutionOut)
def trigger_task(task_id: int, db: Session = Depends(get_db)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    if not acquire_task_lock(db, task.id):
        raise HTTPException(409, "task is locked")
    try:
        attempt = next_attempt_number(db, task.id)
        ex = execute_task_core(db, t, None, attempt)
        schedule_after_result(db, t, ex)
        return ExecutionOut.model_validate(ex)
    finally:
        release_task_lock(db, task_id)


@app.patch("/api/tasks/{task_id}/enable", response_model=TaskOut)
def enable_task(task_id: int, db: Session = Depends(get_db)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    t.enabled = True
    if t.cron_expression:
        try:
            t.next_run_at = CronParser(t.cron_expression).next_after(utcnow() - timedelta(minutes=1))
        except ValueError as e:
            raise HTTPException(400, f"invalid cron: {e}") from e
    elif t.run_at and t.run_at > utcnow():
        t.next_run_at = t.run_at
    db.commit()
    db.refresh(t)
    return task_to_out(t)


@app.patch("/api/tasks/{task_id}/disable", response_model=TaskOut)
def disable_task(task_id: int, db: Session = Depends(get_db)):
    t = db.get(Task, task_id)
    if not t:
        raise HTTPException(404, "task not found")
    t.enabled = False
    db.commit()
    db.refresh(t)
    return task_to_out(t)


@app.post("/api/tasks/{task_id}/dependencies", response_model=DependencyOut)
def add_dependency(task_id: int, body: DependencyCreate, db: Session = Depends(get_db)):
    if task_id == body.depends_on_task_id:
        raise HTTPException(400, "cannot depend on self")
    t = db.get(Task, task_id)
    d = db.get(Task, body.depends_on_task_id)
    if not t or not d:
        raise HTTPException(404, "task not found")
    if path_exists(db, task_id, body.depends_on_task_id):
        raise HTTPException(400, "circular dependency")
    dep = TaskDependency(task_id=task_id, depends_on_id=body.depends_on_task_id)
    db.add(dep)
    try:
        db.commit()
        db.refresh(dep)
    except IntegrityError:
        db.rollback()
        raise HTTPException(409, "dependency already exists")
    return DependencyOut.model_validate(dep)


@app.get("/api/tasks/{task_id}/dependencies", response_model=list[DependencyOut])
def list_dependencies(task_id: int, db: Session = Depends(get_db)):
    if not db.get(Task, task_id):
        raise HTTPException(404, "task not found")
    rows = db.execute(select(TaskDependency).where(TaskDependency.task_id == task_id)).scalars().all()
    return [DependencyOut.model_validate(r) for r in rows]


@app.delete("/api/tasks/{task_id}/dependencies/{dep_id}")
def remove_dependency(task_id: int, dep_id: int, db: Session = Depends(get_db)):
    dep = db.get(TaskDependency, dep_id)
    if not dep or dep.task_id != task_id:
        raise HTTPException(404, "dependency not found")
    db.delete(dep)
    db.commit()
    return {"deleted": True}


def topological_order(db: Session, task_ids: list[int]) -> list[int]:
    ids = set(task_ids)
    for tid in task_ids:
        if not db.get(Task, tid):
            raise HTTPException(404, f"task {tid} not found")

    edges: dict[int, set[int]] = {tid: set() for tid in ids}
    indeg = {tid: 0 for tid in ids}
    rows = db.execute(select(TaskDependency)).scalars().all()
    for r in rows:
        if r.task_id in ids and r.depends_on_id in ids:
            if r.depends_on_id not in edges:
                edges[r.depends_on_id] = set()
            if r.task_id not in edges[r.depends_on_id]:
                edges[r.depends_on_id].add(r.task_id)
                indeg[r.task_id] += 1

    queue = sorted(t for t in ids if indeg[t] == 0)
    out: list[int] = []
    while queue:
        u = queue.pop(0)
        out.append(u)
        for v in edges.get(u, ()):
            indeg[v] -= 1
            if indeg[v] == 0:
                queue.append(v)
    if len(out) != len(ids):
        raise HTTPException(400, "cycle in task subgraph")
    return out


@app.post("/api/dag/execute", response_model=DagStatusOut)
def dag_execute(body: DagExecuteBody, db: Session = Depends(get_db)):
    if not body.task_ids:
        raise HTTPException(400, "task_ids required")
    order = topological_order(db, body.task_ids)
    dag = DagExecution(status="running", started_at=utcnow())
    db.add(dag)
    db.commit()
    db.refresh(dag)
    executions: list[Execution] = []
    for tid in order:
        task = db.get(Task, tid)
        if not acquire_task_lock(db, tid):
            dag.status = "failed"
            dag.completed_at = utcnow()
            db.commit()
            raise HTTPException(409, f"task {tid} locked")
        try:
            attempt = next_attempt_number(db, tid)
            ex = execute_task_core(db, task, dag.id, attempt)
            executions.append(ex)
            if ex.status != "success":
                dag.status = "failed"
                dag.completed_at = utcnow()
                db.commit()
                db.refresh(dag)
                return DagStatusOut(
                    id=dag.id,
                    status=dag.status,
                    started_at=dag.started_at,
                    completed_at=dag.completed_at,
                    executions=[ExecutionOut.model_validate(e) for e in executions],
                )
        finally:
            release_task_lock(db, tid)
    dag.status = "success"
    dag.completed_at = utcnow()
    db.commit()
    db.refresh(dag)
    return DagStatusOut(
        id=dag.id,
        status=dag.status,
        started_at=dag.started_at,
        completed_at=dag.completed_at,
        executions=[ExecutionOut.model_validate(e) for e in executions],
    )


@app.get("/api/dag/{dag_id}/status", response_model=DagStatusOut)
def dag_status(dag_id: int, db: Session = Depends(get_db)):
    dag = db.get(DagExecution, dag_id)
    if not dag:
        raise HTTPException(404, "dag execution not found")
    exs = (
        db.execute(select(Execution).where(Execution.dag_execution_id == dag_id).order_by(Execution.id.asc()))
        .scalars()
        .all()
    )
    return DagStatusOut(
        id=dag.id,
        status=dag.status,
        started_at=dag.started_at,
        completed_at=dag.completed_at,
        executions=[ExecutionOut.model_validate(e) for e in exs],
    )


@app.get("/api/executions", response_model=dict)
def list_executions(
    db: Session = Depends(get_db),
    task_id: Optional[int] = None,
    status: Optional[str] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=200),
):
    q = select(Execution)
    cq = select(func.count()).select_from(Execution)
    if task_id is not None:
        q = q.where(Execution.task_id == task_id)
        cq = cq.where(Execution.task_id == task_id)
    if status:
        q = q.where(Execution.status == status)
        cq = cq.where(Execution.status == status)
    total = db.execute(cq).scalar_one()
    rows = db.execute(q.order_by(Execution.id.desc()).offset(skip).limit(limit)).scalars().all()
    return {"total": total, "items": [ExecutionOut.model_validate(e) for e in rows]}


@app.get("/api/executions/{execution_id}", response_model=ExecutionOut)
def get_execution(execution_id: int, db: Session = Depends(get_db)):
    ex = db.get(Execution, execution_id)
    if not ex:
        raise HTTPException(404, "execution not found")
    return ExecutionOut.model_validate(ex)


@app.get("/api/scheduler/status", response_model=SchedulerStatusOut)
def scheduler_status(db: Session = Depends(get_db)):
    upcoming = (
        db.execute(
            select(Task)
            .where(Task.enabled.is_(True))
            .where(Task.next_run_at.isnot(None))
            .order_by(Task.next_run_at.asc())
            .limit(10)
        )
        .scalars()
        .all()
    )
    return SchedulerStatusOut(
        running=_scheduler_running,
        worker_id=WORKER_ID,
        interval_seconds=SCHEDULER_INTERVAL_SEC,
        next_poll_in_seconds=SCHEDULER_INTERVAL_SEC if _scheduler_running else None,
        upcoming_tasks=[task_to_out(t) for t in upcoming],
    )


@app.post("/api/scheduler/start")
def scheduler_start():
    start_scheduler_bg()
    return {"running": True}


@app.post("/api/scheduler/stop")
def scheduler_stop():
    stop_scheduler_bg()
    return {"running": False}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
