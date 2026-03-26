import re
from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status
from pydantic import BaseModel, Field
from sqlalchemy import ForeignKey, String, UniqueConstraint, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker
from starlette.middleware.base import BaseHTTPMiddleware

DATABASE_URL = "sqlite:///./saas.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class Tenant(Base):
    __tablename__ = "tenants"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    slug: Mapped[str] = mapped_column(String(100), unique=True, nullable=False, index=True)
    plan: Mapped[str] = mapped_column(String(32), nullable=False, default="free")
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))
    is_active: Mapped[bool] = mapped_column(default=True)

    users: Mapped[list["TenantUser"]] = relationship(
        "TenantUser", back_populates="tenant", cascade="all, delete-orphan"
    )
    projects: Mapped[list["Project"]] = relationship(
        "Project", back_populates="tenant", cascade="all, delete-orphan"
    )


class TenantUser(Base):
    __tablename__ = "tenant_users"
    __table_args__ = (UniqueConstraint("tenant_id", "email", name="uq_tenant_user_email"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    username: Mapped[str] = mapped_column(String(100), nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False, default="member")
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="users")


class Project(Base):
    __tablename__ = "projects"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    tenant_id: Mapped[int] = mapped_column(ForeignKey("tenants.id", ondelete="CASCADE"), index=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str] = mapped_column(String(2000), default="")
    created_at: Mapped[datetime] = mapped_column(default=lambda: datetime.now(timezone.utc))

    tenant: Mapped[Tenant] = relationship("Tenant", back_populates="projects")


Base.metadata.create_all(bind=engine)

PLANS = ("free", "starter", "pro", "enterprise")

PLAN_LIMITS: dict[str, dict[str, Optional[int]]] = {
    "free": {"max_users": 5, "max_projects": 3},
    "starter": {"max_users": 25, "max_projects": 10},
    "pro": {"max_users": 100, "max_projects": 50},
    "enterprise": {"max_users": None, "max_projects": None},
}

PLAN_PRICES_USD_MONTHLY: dict[str, float] = {
    "free": 0.0,
    "starter": 29.0,
    "pro": 99.0,
    "enterprise": 499.0,
}


def slugify(s: str) -> str:
    s = s.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    return s.strip("-") or "tenant"


def within_plan_limit(current: int, maximum: Optional[int]) -> bool:
    if maximum is None:
        return True
    return current < maximum


app = FastAPI(title="Multi-Tenant SaaS API")


class TenantMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        request.state.tenant = None
        request.state.tenant_id = None
        raw = request.headers.get("X-Tenant-ID")
        if raw is not None and raw.strip() != "":
            try:
                tid = int(raw.strip())
            except ValueError:
                return self._json_error(status.HTTP_400_BAD_REQUEST, "X-Tenant-ID must be a valid integer")
            db = SessionLocal()
            try:
                tenant = db.scalar(select(Tenant).where(Tenant.id == tid, Tenant.is_active.is_(True)))
                if not tenant:
                    return self._json_error(
                        status.HTTP_404_NOT_FOUND,
                        "Tenant not found or inactive",
                    )
                request.state.tenant = tenant
                request.state.tenant_id = tenant.id
            finally:
                db.close()
        return await call_next(request)

    def _json_error(self, code: int, detail: str):
        from starlette.responses import JSONResponse

        return JSONResponse(status_code=code, content={"detail": detail})


app.add_middleware(TenantMiddleware)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DbSession = Annotated[Session, Depends(get_db)]


def require_active_tenant(request: Request) -> Tenant:
    tenant = getattr(request.state, "tenant", None)
    if tenant is None:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="X-Tenant-ID header required with a valid active tenant id",
        )
    return tenant


CurrentTenant = Annotated[Tenant, Depends(require_active_tenant)]


class TenantCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    slug: Optional[str] = Field(None, max_length=100)
    plan: str = "free"


class TenantUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    plan: Optional[str] = None
    is_active: Optional[bool] = None


class TenantOut(BaseModel):
    id: int
    name: str
    slug: str
    plan: str
    created_at: datetime
    is_active: bool

    model_config = {"from_attributes": True}


class UserCreate(BaseModel):
    username: str = Field(min_length=1, max_length=100)
    email: str = Field(min_length=3, max_length=255)
    role: str = "member"


class UserOut(BaseModel):
    id: int
    tenant_id: int
    username: str
    email: str
    role: str
    created_at: datetime

    model_config = {"from_attributes": True}


class ProjectCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    description: str = ""


class ProjectOut(BaseModel):
    id: int
    tenant_id: int
    name: str
    description: str
    created_at: datetime

    model_config = {"from_attributes": True}


class PlanChange(BaseModel):
    plan: str


def assert_plan_name(plan: str) -> str:
    p = plan.lower().strip()
    if p not in PLAN_LIMITS:
        raise HTTPException(status_code=400, detail=f"Invalid plan. Allowed: {', '.join(PLANS)}")
    return p


@app.post("/api/tenants", response_model=TenantOut, status_code=status.HTTP_201_CREATED)
def create_tenant(body: TenantCreate, db: DbSession):
    plan = assert_plan_name(body.plan)
    slug = slugify(body.slug or body.name)
    if db.scalar(select(Tenant).where(Tenant.slug == slug)):
        raise HTTPException(status_code=409, detail="Slug already exists")
    t = Tenant(name=body.name.strip(), slug=slug, plan=plan)
    db.add(t)
    db.commit()
    db.refresh(t)
    return t


@app.get("/api/tenants", response_model=list[TenantOut])
def list_tenants(db: DbSession):
    return list(db.scalars(select(Tenant).order_by(Tenant.id)))


@app.get("/api/tenants/{slug}", response_model=TenantOut)
def get_tenant_by_slug(slug: str, db: DbSession):
    t = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if not t:
        raise HTTPException(status_code=404, detail="Tenant not found")
    return t


@app.put("/api/tenants/{slug}", response_model=TenantOut)
def update_tenant(slug: str, body: TenantUpdate, db: DbSession):
    t = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if not t:
        raise HTTPException(status_code=404, detail="Tenant not found")
    if body.name is not None:
        t.name = body.name.strip()
    if body.plan is not None:
        t.plan = assert_plan_name(body.plan)
    if body.is_active is not None:
        t.is_active = body.is_active
    db.commit()
    db.refresh(t)
    return t


@app.delete("/api/tenants/{slug}", status_code=status.HTTP_204_NO_CONTENT)
def delete_tenant(slug: str, db: DbSession):
    t = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if not t:
        raise HTTPException(status_code=404, detail="Tenant not found")
    db.delete(t)
    db.commit()
    return None


@app.post("/api/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
def create_user(body: UserCreate, db: DbSession, tenant: CurrentTenant):
    if body.role not in ("admin", "member"):
        raise HTTPException(status_code=400, detail="role must be admin or member")
    limits = PLAN_LIMITS.get(tenant.plan, PLAN_LIMITS["free"])
    user_count = db.scalar(
        select(func.count()).select_from(TenantUser).where(TenantUser.tenant_id == tenant.id)
    )
    if not within_plan_limit(int(user_count or 0), limits["max_users"]):
        raise HTTPException(status_code=403, detail="User limit reached for current plan")
    if db.scalar(
        select(TenantUser).where(TenantUser.tenant_id == tenant.id, TenantUser.email == body.email.strip())
    ):
        raise HTTPException(status_code=409, detail="Email already exists in this tenant")
    u = TenantUser(
        tenant_id=tenant.id,
        username=body.username.strip(),
        email=body.email.strip().lower(),
        role=body.role,
    )
    db.add(u)
    db.commit()
    db.refresh(u)
    return u


@app.get("/api/users", response_model=list[UserOut])
def list_users(db: DbSession, tenant: CurrentTenant):
    return list(
        db.scalars(select(TenantUser).where(TenantUser.tenant_id == tenant.id).order_by(TenantUser.id))
    )


@app.post("/api/projects", response_model=ProjectOut, status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectCreate, db: DbSession, tenant: CurrentTenant):
    limits = PLAN_LIMITS.get(tenant.plan, PLAN_LIMITS["free"])
    proj_count = db.scalar(
        select(func.count()).select_from(Project).where(Project.tenant_id == tenant.id)
    )
    if not within_plan_limit(int(proj_count or 0), limits["max_projects"]):
        raise HTTPException(status_code=403, detail="Project limit reached for current plan")
    p = Project(
        tenant_id=tenant.id,
        name=body.name.strip(),
        description=body.description or "",
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@app.get("/api/projects", response_model=list[ProjectOut])
def list_projects(db: DbSession, tenant: CurrentTenant):
    return list(db.scalars(select(Project).where(Project.tenant_id == tenant.id).order_by(Project.id)))


@app.get("/api/projects/{project_id}", response_model=ProjectOut)
def get_project(project_id: int, db: DbSession, tenant: CurrentTenant):
    p = db.scalar(
        select(Project).where(Project.id == project_id, Project.tenant_id == tenant.id)
    )
    if not p:
        raise HTTPException(status_code=404, detail="Project not found")
    return p


@app.get("/api/subscription")
def get_subscription(db: DbSession, tenant: CurrentTenant):
    limits = PLAN_LIMITS.get(tenant.plan, PLAN_LIMITS["free"])
    user_count = int(
        db.scalar(select(func.count()).select_from(TenantUser).where(TenantUser.tenant_id == tenant.id)) or 0
    )
    project_count = int(
        db.scalar(select(func.count()).select_from(Project).where(Project.tenant_id == tenant.id)) or 0
    )
    return {
        "tenant_id": tenant.id,
        "slug": tenant.slug,
        "plan": tenant.plan,
        "limits": {"max_users": limits["max_users"], "max_projects": limits["max_projects"]},
        "usage": {"users": user_count, "projects": project_count},
        "price_usd_monthly": PLAN_PRICES_USD_MONTHLY.get(tenant.plan, 0.0),
    }


@app.put("/api/subscription/plan")
def change_plan(body: PlanChange, db: DbSession, tenant: CurrentTenant):
    new_plan = assert_plan_name(body.plan)
    tenant.plan = new_plan
    db.commit()
    db.refresh(tenant)
    return {"tenant_id": tenant.id, "plan": tenant.plan}


@app.get("/api/plans")
def list_plans():
    return [
        {
            "plan": name,
            "max_users": PLAN_LIMITS[name]["max_users"],
            "max_projects": PLAN_LIMITS[name]["max_projects"],
            "price_usd_monthly": PLAN_PRICES_USD_MONTHLY.get(name, 0.0),
        }
        for name in PLANS
    ]


@app.get("/api/admin/stats")
def admin_stats(db: DbSession):
    tenant_count = int(db.scalar(select(func.count()).select_from(Tenant)) or 0)
    user_count = int(db.scalar(select(func.count()).select_from(TenantUser)) or 0)
    rows = db.execute(select(Tenant.plan, func.count()).group_by(Tenant.plan)).all()
    revenue_by_plan = []
    for plan, cnt in rows:
        price = PLAN_PRICES_USD_MONTHLY.get(plan, 0.0)
        revenue_by_plan.append(
            {"plan": plan, "tenant_count": int(cnt), "mrr_usd": float(price) * int(cnt)}
        )
    total_mrr = sum(x["mrr_usd"] for x in revenue_by_plan)
    return {
        "tenant_count": tenant_count,
        "user_count": user_count,
        "revenue_by_plan": revenue_by_plan,
        "total_mrr_usd": total_mrr,
    }


@app.get("/api/admin/tenants/{slug}/usage")
def admin_tenant_usage(slug: str, db: DbSession):
    t = db.scalar(select(Tenant).where(Tenant.slug == slug))
    if not t:
        raise HTTPException(status_code=404, detail="Tenant not found")
    limits = PLAN_LIMITS.get(t.plan, PLAN_LIMITS["free"])
    user_count = int(
        db.scalar(select(func.count()).select_from(TenantUser).where(TenantUser.tenant_id == t.id)) or 0
    )
    project_count = int(
        db.scalar(select(func.count()).select_from(Project).where(Project.tenant_id == t.id)) or 0
    )
    return {
        "tenant": TenantOut.model_validate(t).model_dump(),
        "limits": {"max_users": limits["max_users"], "max_projects": limits["max_projects"]},
        "usage": {"users": user_count, "projects": project_count},
        "mrr_usd": PLAN_PRICES_USD_MONTHLY.get(t.plan, 0.0),
    }


@app.get("/health")
def health():
    return {"status": "ok"}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
