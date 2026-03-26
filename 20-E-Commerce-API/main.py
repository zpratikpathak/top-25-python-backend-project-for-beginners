from contextlib import asynccontextmanager
from datetime import datetime, timezone
from decimal import Decimal
from enum import Enum
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, Header, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    Numeric,
    String,
    Text,
    create_engine,
    func,
    select,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

DATABASE_URL = "sqlite:///./ecommerce.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class Product(Base):
    __tablename__ = "products"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    stock: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    category: Mapped[Optional[str]] = mapped_column(String(128), nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )


class CartItem(Base):
    __tablename__ = "cart_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)

    product: Mapped["Product"] = relationship()


class OrderStatus(str, Enum):
    pending = "pending"
    confirmed = "confirmed"
    shipped = "shipped"
    delivered = "delivered"
    cancelled = "cancelled"


class Order(Base):
    __tablename__ = "orders"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(String(128), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(32), nullable=False, default=OrderStatus.pending.value)
    total_amount: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)
    shipping_address: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now()
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), onupdate=func.now()
    )

    items: Mapped[list["OrderItem"]] = relationship(
        back_populates="order", cascade="all, delete-orphan"
    )


class OrderItem(Base):
    __tablename__ = "order_items"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    order_id: Mapped[int] = mapped_column(ForeignKey("orders.id"), nullable=False)
    product_id: Mapped[int] = mapped_column(ForeignKey("products.id"), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer, nullable=False)
    unit_price: Mapped[Decimal] = mapped_column(Numeric(12, 2), nullable=False)

    order: Mapped["Order"] = relationship(back_populates="items")
    product: Mapped["Product"] = relationship()


def now_utc() -> datetime:
    return datetime.now(timezone.utc)


VALID_STATUS_TRANSITIONS: dict[str, set[str]] = {
    OrderStatus.pending.value: {OrderStatus.confirmed.value, OrderStatus.cancelled.value},
    OrderStatus.confirmed.value: {OrderStatus.shipped.value, OrderStatus.cancelled.value},
    OrderStatus.shipped.value: {OrderStatus.delivered.value, OrderStatus.cancelled.value},
    OrderStatus.delivered.value: set(),
    OrderStatus.cancelled.value: set(),
}


@asynccontextmanager
async def lifespan(app: FastAPI):
    Base.metadata.create_all(bind=engine)
    yield


app = FastAPI(title="E-Commerce API", lifespan=lifespan)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DbSession = Annotated[Session, Depends(get_db)]


def get_session_id(
    x_session_id: Annotated[Optional[str], Header(alias="X-Session-ID")] = None,
    session_id: Annotated[Optional[str], Query(alias="session_id")] = None,
) -> str:
    sid = x_session_id or session_id
    if not sid or not sid.strip():
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Provide X-Session-ID header or session_id query parameter",
        )
    return sid.strip()


SessionIdDep = Annotated[str, Depends(get_session_id)]


class ProductCreate(BaseModel):
    name: str = Field(..., min_length=1, max_length=255)
    description: Optional[str] = None
    price: Decimal = Field(..., ge=0)
    stock: int = Field(..., ge=0)
    category: Optional[str] = Field(None, max_length=128)


class ProductUpdate(BaseModel):
    name: Optional[str] = Field(None, min_length=1, max_length=255)
    description: Optional[str] = None
    price: Optional[Decimal] = Field(None, ge=0)
    stock: Optional[int] = Field(None, ge=0)
    category: Optional[str] = Field(None, max_length=128)


class ProductResponse(BaseModel):
    id: int
    name: str
    description: Optional[str]
    price: Decimal
    stock: int
    category: Optional[str]
    created_at: datetime

    model_config = {"from_attributes": True}


class CartItemAdd(BaseModel):
    product_id: int
    quantity: int = Field(..., ge=1)


class CartItemUpdate(BaseModel):
    quantity: int = Field(..., ge=1)


class CartLineResponse(BaseModel):
    product_id: int
    product_name: str
    quantity: int
    unit_price: Decimal
    line_total: Decimal


class CartResponse(BaseModel):
    session_id: str
    items: list[CartLineResponse]
    subtotal: Decimal
    item_count: int


class OrderCreate(BaseModel):
    shipping_address: str = Field(..., min_length=1)


class OrderItemResponse(BaseModel):
    product_id: int
    product_name: str
    quantity: int
    unit_price: Decimal
    line_total: Decimal


class OrderResponse(BaseModel):
    id: int
    session_id: str
    status: str
    total_amount: Decimal
    shipping_address: str
    created_at: datetime
    updated_at: datetime
    items: list[OrderItemResponse]

    model_config = {"from_attributes": True}


class OrderStatusPatch(BaseModel):
    status: OrderStatus


@app.post("/api/products", response_model=ProductResponse, status_code=status.HTTP_201_CREATED)
def create_product(payload: ProductCreate, db: DbSession):
    p = Product(
        name=payload.name,
        description=payload.description,
        price=payload.price,
        stock=payload.stock,
        category=payload.category,
    )
    db.add(p)
    db.commit()
    db.refresh(p)
    return p


@app.get("/api/products", response_model=dict)
def list_products(
    db: DbSession,
    category: Optional[str] = None,
    min_price: Optional[Decimal] = None,
    max_price: Optional[Decimal] = None,
    search: Optional[str] = None,
    in_stock: Optional[bool] = None,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    conditions = []
    if category is not None:
        conditions.append(Product.category == category)
    if min_price is not None:
        conditions.append(Product.price >= min_price)
    if max_price is not None:
        conditions.append(Product.price <= max_price)
    if search:
        term = f"%{search.lower()}%"
        desc = func.coalesce(Product.description, "")
        conditions.append(
            func.lower(Product.name).like(term) | func.lower(desc).like(term)
        )
    if in_stock is True:
        conditions.append(Product.stock > 0)
    elif in_stock is False:
        conditions.append(Product.stock == 0)

    q = select(Product)
    count_q = select(func.count(Product.id))
    if conditions:
        q = q.where(*conditions)
        count_q = count_q.where(*conditions)
    total = db.scalar(count_q) or 0
    q = q.order_by(Product.id.desc()).offset(skip).limit(limit)
    rows = db.scalars(q).all()
    return {
        "total": total,
        "skip": skip,
        "limit": limit,
        "items": [ProductResponse.model_validate(r) for r in rows],
    }


@app.get("/api/products/{product_id}", response_model=ProductResponse)
def get_product(product_id: int, db: DbSession):
    p = db.get(Product, product_id)
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    return p


@app.put("/api/products/{product_id}", response_model=ProductResponse)
def update_product(product_id: int, payload: ProductUpdate, db: DbSession):
    p = db.get(Product, product_id)
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    data = payload.model_dump(exclude_unset=True)
    for k, v in data.items():
        setattr(p, k, v)
    db.commit()
    db.refresh(p)
    return p


@app.delete("/api/products/{product_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_product(product_id: int, db: DbSession):
    p = db.get(Product, product_id)
    if not p:
        raise HTTPException(status_code=404, detail="Product not found")
    db.delete(p)
    db.commit()


def build_cart_response(db: Session, session_id: str) -> CartResponse:
    items_db = db.scalars(
        select(CartItem).where(CartItem.session_id == session_id)
    ).all()
    lines: list[CartLineResponse] = []
    subtotal = Decimal("0")
    count = 0
    for ci in items_db:
        prod = db.get(Product, ci.product_id)
        if not prod:
            continue
        line_total = prod.price * ci.quantity
        subtotal += line_total
        count += ci.quantity
        lines.append(
            CartLineResponse(
                product_id=prod.id,
                product_name=prod.name,
                quantity=ci.quantity,
                unit_price=prod.price,
                line_total=line_total,
            )
        )
    return CartResponse(
        session_id=session_id,
        items=lines,
        subtotal=subtotal,
        item_count=count,
    )


@app.post("/api/cart/items", response_model=CartResponse)
def add_cart_item(payload: CartItemAdd, db: DbSession, session_id: SessionIdDep):
    prod = db.get(Product, payload.product_id)
    if not prod:
        raise HTTPException(status_code=404, detail="Product not found")
    existing = db.scalar(
        select(CartItem).where(
            CartItem.session_id == session_id,
            CartItem.product_id == payload.product_id,
        )
    )
    if existing:
        existing.quantity += payload.quantity
    else:
        db.add(
            CartItem(
                session_id=session_id,
                product_id=payload.product_id,
                quantity=payload.quantity,
            )
        )
    db.commit()
    return build_cart_response(db, session_id)


@app.get("/api/cart", response_model=CartResponse)
def get_cart(db: DbSession, session_id: SessionIdDep):
    return build_cart_response(db, session_id)


@app.put("/api/cart/items/{product_id}", response_model=CartResponse)
def update_cart_item(
    product_id: int, payload: CartItemUpdate, db: DbSession, session_id: SessionIdDep
):
    ci = db.scalar(
        select(CartItem).where(
            CartItem.session_id == session_id,
            CartItem.product_id == product_id,
        )
    )
    if not ci:
        raise HTTPException(status_code=404, detail="Cart item not found")
    ci.quantity = payload.quantity
    db.commit()
    return build_cart_response(db, session_id)


@app.delete("/api/cart/items/{product_id}", response_model=CartResponse)
def remove_cart_item(product_id: int, db: DbSession, session_id: SessionIdDep):
    ci = db.scalar(
        select(CartItem).where(
            CartItem.session_id == session_id,
            CartItem.product_id == product_id,
        )
    )
    if ci:
        db.delete(ci)
        db.commit()
    return build_cart_response(db, session_id)


@app.delete("/api/cart", response_model=CartResponse)
def clear_cart(db: DbSession, session_id: SessionIdDep):
    for ci in db.scalars(select(CartItem).where(CartItem.session_id == session_id)).all():
        db.delete(ci)
    db.commit()
    return build_cart_response(db, session_id)


@app.post("/api/orders", response_model=OrderResponse, status_code=status.HTTP_201_CREATED)
def create_order(payload: OrderCreate, db: DbSession, session_id: SessionIdDep):
    cart_items = db.scalars(
        select(CartItem).where(CartItem.session_id == session_id)
    ).all()
    if not cart_items:
        raise HTTPException(status_code=400, detail="Cart is empty")

    total = Decimal("0")
    order_lines: list[tuple[Product, int, Decimal]] = []

    for ci in cart_items:
        prod = db.get(Product, ci.product_id)
        if not prod:
            raise HTTPException(
                status_code=400,
                detail=f"Product id {ci.product_id} no longer exists",
            )
        if prod.stock < ci.quantity:
            raise HTTPException(
                status_code=400,
                detail=f"Insufficient stock for '{prod.name}' (available: {prod.stock})",
            )
        line_total = prod.price * ci.quantity
        total += line_total
        order_lines.append((prod, ci.quantity, prod.price))

    order = Order(
        session_id=session_id,
        status=OrderStatus.pending.value,
        total_amount=total,
        shipping_address=payload.shipping_address,
        updated_at=now_utc(),
    )
    db.add(order)
    db.flush()

    for prod, qty, unit_price in order_lines:
        db.add(
            OrderItem(
                order_id=order.id,
                product_id=prod.id,
                quantity=qty,
                unit_price=unit_price,
            )
        )
        prod.stock -= qty

    for ci in cart_items:
        db.delete(ci)

    db.commit()
    db.refresh(order)
    return order_to_response(db, order)


def order_to_response(db: Session, order: Order) -> OrderResponse:
    db.refresh(order)
    item_responses: list[OrderItemResponse] = []
    for oi in order.items:
        prod = db.get(Product, oi.product_id)
        name = prod.name if prod else f"product#{oi.product_id}"
        item_responses.append(
            OrderItemResponse(
                product_id=oi.product_id,
                product_name=name,
                quantity=oi.quantity,
                unit_price=oi.unit_price,
                line_total=oi.unit_price * oi.quantity,
            )
        )
    return OrderResponse(
        id=order.id,
        session_id=order.session_id,
        status=order.status,
        total_amount=order.total_amount,
        shipping_address=order.shipping_address,
        created_at=order.created_at,
        updated_at=order.updated_at,
        items=item_responses,
    )


@app.get("/api/orders", response_model=list[OrderResponse])
def list_orders(db: DbSession, session_id: SessionIdDep):
    orders = db.scalars(
        select(Order)
        .where(Order.session_id == session_id)
        .order_by(Order.id.desc())
    ).all()
    return [order_to_response(db, o) for o in orders]


@app.get("/api/orders/{order_id}", response_model=OrderResponse)
def get_order(order_id: int, db: DbSession, session_id: SessionIdDep):
    order = db.get(Order, order_id)
    if not order or order.session_id != session_id:
        raise HTTPException(status_code=404, detail="Order not found")
    return order_to_response(db, order)


@app.patch("/api/orders/{order_id}/status", response_model=OrderResponse)
def patch_order_status(
    order_id: int, payload: OrderStatusPatch, db: DbSession, session_id: SessionIdDep
):
    order = db.get(Order, order_id)
    if not order or order.session_id != session_id:
        raise HTTPException(status_code=404, detail="Order not found")
    new_status = payload.status.value
    allowed = VALID_STATUS_TRANSITIONS.get(order.status, set())
    if new_status not in allowed:
        raise HTTPException(
            status_code=400,
            detail=f"Cannot transition from '{order.status}' to '{new_status}'",
        )
    order.status = new_status
    order.updated_at = now_utc()
    db.commit()
    db.refresh(order)
    return order_to_response(db, order)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
