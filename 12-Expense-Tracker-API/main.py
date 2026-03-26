from datetime import date, datetime
from typing import Annotated, Literal, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import Date, Float, String, UniqueConstraint, create_engine, func, select
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, sessionmaker

DATABASE_URL = "sqlite:///./expense_tracker.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class Transaction(Base):
    __tablename__ = "transactions"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    amount: Mapped[float] = mapped_column(Float)
    txn_type: Mapped[str] = mapped_column("type", String(16))
    category: Mapped[str] = mapped_column(String(128))
    description: Mapped[str] = mapped_column(String(512), default="")
    txn_date: Mapped[date] = mapped_column("date", Date)
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


class Budget(Base):
    __tablename__ = "budgets"
    __table_args__ = (UniqueConstraint("category", "month", name="uq_budget_category_month"),)

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    category: Mapped[str] = mapped_column(String(128))
    amount: Mapped[float] = mapped_column(Float)
    month: Mapped[str] = mapped_column(String(7))
    created_at: Mapped[datetime] = mapped_column(default=datetime.utcnow)


Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DbDep = Annotated[Session, Depends(get_db)]


class TransactionCreate(BaseModel):
    amount: float = Field(gt=0)
    type: Literal["income", "expense"]
    category: str = Field(min_length=1, max_length=128)
    description: str = Field(default="", max_length=512)
    date: date


class TransactionUpdate(BaseModel):
    amount: Optional[float] = Field(default=None, gt=0)
    type: Optional[Literal["income", "expense"]] = None
    category: Optional[str] = Field(default=None, min_length=1, max_length=128)
    description: Optional[str] = Field(default=None, max_length=512)
    date: Optional[date] = None


class TransactionOut(BaseModel):
    id: int
    amount: float
    type: Literal["income", "expense"]
    category: str
    description: str
    date: date
    created_at: datetime

    model_config = {"from_attributes": True}

    @classmethod
    def from_orm_tx(cls, t: Transaction) -> "TransactionOut":
        return cls(
            id=t.id,
            amount=t.amount,
            type=t.txn_type,  # type: ignore[arg-type]
            category=t.category,
            description=t.description,
            date=t.txn_date,
            created_at=t.created_at,
        )


class BudgetCreate(BaseModel):
    category: str = Field(min_length=1, max_length=128)
    amount: float = Field(ge=0)
    month: str = Field(pattern=r"^\d{4}-\d{2}$")

    @field_validator("month")
    @classmethod
    def month_valid(cls, v: str) -> str:
        y, m = map(int, v.split("-"))
        if not (1 <= m <= 12):
            raise ValueError("month must be 01-12")
        return f"{y:04d}-{m:02d}"


class BudgetOut(BaseModel):
    id: int
    category: str
    amount: float
    month: str
    created_at: datetime

    model_config = {"from_attributes": True}


class BudgetWithSpent(BaseModel):
    id: int
    category: str
    budget_amount: float
    spent: float
    remaining: float
    month: str


class SummaryOut(BaseModel):
    total_income: float
    total_expenses: float
    balance: float


class MonthlyCategoryRow(BaseModel):
    category: str
    income: float
    expenses: float


class CategoryTotalsOut(BaseModel):
    category: str
    total_income: float
    total_expenses: float


app = FastAPI(title="Expense Tracker API", version="1.0.0")


@app.post("/api/transactions", response_model=TransactionOut, status_code=201)
def create_transaction(payload: TransactionCreate, db: DbDep):
    t = Transaction(
        amount=payload.amount,
        txn_type=payload.type,
        category=payload.category.strip(),
        description=payload.description,
        txn_date=payload.date,
    )
    db.add(t)
    db.commit()
    db.refresh(t)
    return TransactionOut.from_orm_tx(t)


@app.get("/api/transactions", response_model=list[TransactionOut])
def list_transactions(
    db: DbDep,
    type: Optional[Literal["income", "expense"]] = Query(default=None),
    category: Optional[str] = Query(default=None),
    start_date: Optional[date] = Query(default=None),
    end_date: Optional[date] = Query(default=None),
    skip: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=200),
):
    q = select(Transaction).order_by(Transaction.txn_date.desc(), Transaction.id.desc())
    if type is not None:
        q = q.where(Transaction.txn_type == type)
    if category is not None:
        q = q.where(Transaction.category == category.strip())
    if start_date is not None:
        q = q.where(Transaction.txn_date >= start_date)
    if end_date is not None:
        q = q.where(Transaction.txn_date <= end_date)
    q = q.offset(skip).limit(limit)
    rows = db.scalars(q).all()
    return [TransactionOut.from_orm_tx(t) for t in rows]


@app.get("/api/transactions/{transaction_id}", response_model=TransactionOut)
def get_transaction(transaction_id: int, db: DbDep):
    t = db.get(Transaction, transaction_id)
    if not t:
        raise HTTPException(status_code=404, detail="Transaction not found")
    return TransactionOut.from_orm_tx(t)


@app.put("/api/transactions/{transaction_id}", response_model=TransactionOut)
def update_transaction(transaction_id: int, payload: TransactionUpdate, db: DbDep):
    t = db.get(Transaction, transaction_id)
    if not t:
        raise HTTPException(status_code=404, detail="Transaction not found")
    data = payload.model_dump(exclude_unset=True)
    if "type" in data:
        t.txn_type = data.pop("type")
    if "date" in data:
        t.txn_date = data.pop("date")
    for k, v in data.items():
        setattr(t, k, v.strip() if k == "category" and isinstance(v, str) else v)
    db.commit()
    db.refresh(t)
    return TransactionOut.from_orm_tx(t)


@app.delete("/api/transactions/{transaction_id}", status_code=204)
def delete_transaction(transaction_id: int, db: DbDep):
    t = db.get(Transaction, transaction_id)
    if not t:
        raise HTTPException(status_code=404, detail="Transaction not found")
    db.delete(t)
    db.commit()
    return None


@app.get("/api/summary", response_model=SummaryOut)
def get_summary(db: DbDep):
    income = db.scalar(
        select(func.coalesce(func.sum(Transaction.amount), 0.0)).where(Transaction.txn_type == "income")
    )
    expenses = db.scalar(
        select(func.coalesce(func.sum(Transaction.amount), 0.0)).where(Transaction.txn_type == "expense")
    )
    income_f = float(income or 0)
    expenses_f = float(expenses or 0)
    return SummaryOut(
        total_income=income_f,
        total_expenses=expenses_f,
        balance=income_f - expenses_f,
    )


@app.get("/api/summary/monthly", response_model=list[MonthlyCategoryRow])
def monthly_summary(
    db: DbDep,
    year: int = Query(..., ge=1, le=9999),
    month: int = Query(..., ge=1, le=12),
):
    start = date(year, month, 1)
    if month == 12:
        end = date(year + 1, 1, 1)
    else:
        end = date(year, month + 1, 1)

    subq = (
        select(
            Transaction.category,
            Transaction.txn_type,
            func.coalesce(func.sum(Transaction.amount), 0.0).label("total"),
        )
        .where(Transaction.txn_date >= start, Transaction.txn_date < end)
        .group_by(Transaction.category, Transaction.txn_type)
    )
    rows = db.execute(subq).all()
    by_cat: dict[str, dict[str, float]] = {}
    for cat, txn_type, total in rows:
        if cat not in by_cat:
            by_cat[cat] = {"income": 0.0, "expenses": 0.0}
        key = "income" if txn_type == "income" else "expenses"
        by_cat[cat][key] = float(total)

    return [
        MonthlyCategoryRow(category=c, income=v["income"], expenses=v["expenses"])
        for c, v in sorted(by_cat.items(), key=lambda x: x[0].lower())
    ]


@app.get("/api/categories", response_model=list[CategoryTotalsOut])
def list_categories(db: DbDep):
    subq = (
        select(
            Transaction.category,
            Transaction.txn_type,
            func.coalesce(func.sum(Transaction.amount), 0.0).label("total"),
        )
        .group_by(Transaction.category, Transaction.txn_type)
    )
    rows = db.execute(subq).all()
    by_cat: dict[str, dict[str, float]] = {}
    for cat, txn_type, total in rows:
        if cat not in by_cat:
            by_cat[cat] = {"income": 0.0, "expenses": 0.0}
        key = "total_income" if txn_type == "income" else "total_expenses"
        by_cat[cat][key] = float(total)

    return [
        CategoryTotalsOut(
            category=c,
            total_income=v.get("total_income", 0.0),
            total_expenses=v.get("total_expenses", 0.0),
        )
        for c, v in sorted(by_cat.items(), key=lambda x: x[0].lower())
    ]


def _spent_for_category_month(db: Session, category: str, month_str: str) -> float:
    y, m = map(int, month_str.split("-"))
    start = date(y, m, 1)
    if m == 12:
        end = date(y + 1, 1, 1)
    else:
        end = date(y, m + 1, 1)
    q = select(func.coalesce(func.sum(Transaction.amount), 0.0)).where(
        Transaction.txn_type == "expense",
        Transaction.category == category,
        Transaction.txn_date >= start,
        Transaction.txn_date < end,
    )
    return float(db.scalar(q) or 0)


@app.post("/api/budgets", response_model=BudgetOut, status_code=201)
def create_or_update_budget(payload: BudgetCreate, db: DbDep):
    existing = db.scalar(
        select(Budget).where(Budget.category == payload.category.strip(), Budget.month == payload.month)
    )
    if existing:
        existing.amount = payload.amount
        db.commit()
        db.refresh(existing)
        return existing
    b = Budget(category=payload.category.strip(), amount=payload.amount, month=payload.month)
    db.add(b)
    db.commit()
    db.refresh(b)
    return b


@app.get("/api/budgets", response_model=list[BudgetWithSpent])
def list_budgets(
    db: DbDep,
    month: str = Query(..., pattern=r"^\d{4}-\d{2}$"),
):
    y, m = map(int, month.split("-"))
    if not (1 <= m <= 12):
        raise HTTPException(status_code=400, detail="month must be 01-12")
    month_norm = f"{y:04d}-{m:02d}"

    budgets = db.scalars(select(Budget).where(Budget.month == month_norm).order_by(Budget.category)).all()
    out: list[BudgetWithSpent] = []
    for b in budgets:
        spent = _spent_for_category_month(db, b.category, month_norm)
        out.append(
            BudgetWithSpent(
                id=b.id,
                category=b.category,
                budget_amount=b.amount,
                spent=spent,
                remaining=b.amount - spent,
                month=b.month,
            )
        )
    return out


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
