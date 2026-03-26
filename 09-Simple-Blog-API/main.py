from datetime import datetime, timezone
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, HTTPException, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Table,
    Text,
    create_engine,
    func,
    or_,
    select,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, selectinload, sessionmaker

DATABASE_URL = "sqlite:///./blog.db"

engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


post_tags = Table(
    "post_tags",
    Base.metadata,
    Column("post_id", Integer, ForeignKey("posts.id", ondelete="CASCADE"), primary_key=True),
    Column("tag_id", Integer, ForeignKey("tags.id", ondelete="CASCADE"), primary_key=True),
)


class Category(Base):
    __tablename__ = "categories"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)

    posts: Mapped[list["Post"]] = relationship(back_populates="category")


class Tag(Base):
    __tablename__ = "tags"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)

    posts: Mapped[list["Post"]] = relationship(secondary=post_tags, back_populates="tags")


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    category_id: Mapped[int] = mapped_column(ForeignKey("categories.id"), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(DateTime, server_default=func.now(), onupdate=func.now())

    category: Mapped["Category"] = relationship(back_populates="posts")
    tags: Mapped[list["Tag"]] = relationship(secondary=post_tags, back_populates="posts")


Base.metadata.create_all(bind=engine)


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DbSession = Annotated[Session, Depends(get_db)]


class CategoryCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class CategoryRead(BaseModel):
    id: int
    name: str

    model_config = {"from_attributes": True}


class TagCreate(BaseModel):
    name: str = Field(min_length=1, max_length=255)


class TagRead(BaseModel):
    id: int
    name: str

    model_config = {"from_attributes": True}


class PostCreate(BaseModel):
    title: str = Field(min_length=1, max_length=500)
    content: str = Field(min_length=1)
    category_id: int
    tag_ids: Optional[list[int]] = None


class PostUpdate(BaseModel):
    title: Optional[str] = Field(default=None, min_length=1, max_length=500)
    content: Optional[str] = Field(default=None, min_length=1)
    category_id: Optional[int] = None
    tag_ids: Optional[list[int]] = None


class PostRead(BaseModel):
    id: int
    title: str
    content: str
    category_id: int
    created_at: datetime
    updated_at: datetime
    category: CategoryRead
    tags: list[TagRead]

    model_config = {"from_attributes": True}


class PostListResponse(BaseModel):
    items: list[PostRead]
    total: int
    page: int
    per_page: int


app = FastAPI(title="Simple Blog API", version="1.0.0")


def _touch_updated(post: Post) -> None:
    post.updated_at = datetime.now(timezone.utc)


@app.post("/api/categories", response_model=CategoryRead, status_code=status.HTTP_201_CREATED)
def create_category(body: CategoryCreate, db: DbSession):
    cat = Category(name=body.name.strip())
    db.add(cat)
    try:
        db.commit()
        db.refresh(cat)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Category name already exists")
    return cat


@app.get("/api/categories", response_model=list[CategoryRead])
def list_categories(db: DbSession):
    return db.scalars(select(Category).order_by(Category.name)).all()


@app.post("/api/tags", response_model=TagRead, status_code=status.HTTP_201_CREATED)
def create_tag(body: TagCreate, db: DbSession):
    tag = Tag(name=body.name.strip())
    db.add(tag)
    try:
        db.commit()
        db.refresh(tag)
    except IntegrityError:
        db.rollback()
        raise HTTPException(status.HTTP_409_CONFLICT, detail="Tag name already exists")
    return tag


@app.get("/api/tags", response_model=list[TagRead])
def list_tags(db: DbSession):
    return db.scalars(select(Tag).order_by(Tag.name)).all()


@app.post("/api/posts", response_model=PostRead, status_code=status.HTTP_201_CREATED)
def create_post(body: PostCreate, db: DbSession):
    cat = db.get(Category, body.category_id)
    if not cat:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Category not found")

    post = Post(title=body.title, content=body.content, category_id=body.category_id)
    if body.tag_ids:
        tags = db.scalars(select(Tag).where(Tag.id.in_(body.tag_ids))).all()
        if len(tags) != len(set(body.tag_ids)):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="One or more tags not found")
        post.tags = tags

    db.add(post)
    db.commit()
    db.refresh(post)
    post = db.scalars(
        select(Post)
        .where(Post.id == post.id)
        .options(selectinload(Post.category), selectinload(Post.tags))
    ).first()
    return post


def _filtered_post_ids_stmt(
    category_id: Optional[int],
    tag_id: Optional[int],
    search: Optional[str],
):
    id_stmt = select(Post.id)
    if category_id is not None:
        id_stmt = id_stmt.where(Post.category_id == category_id)
    if search:
        term = f"%{search.strip()}%"
        id_stmt = id_stmt.where(or_(Post.title.ilike(term), Post.content.ilike(term)))
    if tag_id is not None:
        id_stmt = id_stmt.join(post_tags, Post.id == post_tags.c.post_id).where(post_tags.c.tag_id == tag_id)
    return id_stmt.distinct()


@app.get("/api/posts", response_model=PostListResponse)
def list_posts(
    db: DbSession,
    page: int = Query(1, ge=1),
    per_page: int = Query(10, ge=1, le=100),
    category: Optional[int] = Query(None, description="Filter by category id"),
    tag: Optional[int] = Query(None, description="Filter by tag id"),
    search: Optional[str] = Query(None, description="Search in title and content"),
):
    id_stmt = _filtered_post_ids_stmt(category, tag, search)
    sq = id_stmt.subquery()
    total = db.scalar(select(func.count()).select_from(sq)) or 0

    skip = (page - 1) * per_page
    posts_stmt = (
        select(Post)
        .where(Post.id.in_(select(sq.c.id)))
        .options(selectinload(Post.category), selectinload(Post.tags))
        .order_by(Post.created_at.desc())
        .offset(skip)
        .limit(per_page)
    )
    items = list(db.scalars(posts_stmt).unique().all())
    return PostListResponse(items=items, total=total, page=page, per_page=per_page)


@app.get("/api/posts/{post_id}", response_model=PostRead)
def get_post(post_id: int, db: DbSession):
    post = db.scalars(
        select(Post)
        .where(Post.id == post_id)
        .options(selectinload(Post.category), selectinload(Post.tags))
    ).first()
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Post not found")
    return post


@app.put("/api/posts/{post_id}", response_model=PostRead)
def update_post(post_id: int, body: PostUpdate, db: DbSession):
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Post not found")

    if body.title is not None:
        post.title = body.title
    if body.content is not None:
        post.content = body.content
    if body.category_id is not None:
        if not db.get(Category, body.category_id):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Category not found")
        post.category_id = body.category_id
    if body.tag_ids is not None:
        tags = db.scalars(select(Tag).where(Tag.id.in_(body.tag_ids))).all()
        if len(tags) != len(set(body.tag_ids)):
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail="One or more tags not found")
        post.tags = tags

    _touch_updated(post)
    db.commit()
    db.refresh(post)
    post = db.scalars(
        select(Post)
        .where(Post.id == post_id)
        .options(selectinload(Post.category), selectinload(Post.tags))
    ).first()
    return post


@app.delete("/api/posts/{post_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_post(post_id: int, db: DbSession):
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status.HTTP_404_NOT_FOUND, detail="Post not found")
    db.delete(post)
    db.commit()
    return None
