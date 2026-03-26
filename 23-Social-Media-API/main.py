from datetime import datetime
from typing import Annotated, Optional

from fastapi import Depends, FastAPI, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import (
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
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

DATABASE_URL = "sqlite:///./social.db"
engine = create_engine(DATABASE_URL, connect_args={"check_same_thread": False})
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


class Base(DeclarativeBase):
    pass


class User(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    username: Mapped[str] = mapped_column(String(80), unique=True, index=True, nullable=False)
    email: Mapped[str] = mapped_column(String(255), nullable=False)
    bio: Mapped[str] = mapped_column(Text, default="")
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    posts: Mapped[list["Post"]] = relationship("Post", back_populates="author", cascade="all, delete-orphan")
    likes: Mapped[list["Like"]] = relationship("Like", back_populates="user", cascade="all, delete-orphan")
    comments: Mapped[list["Comment"]] = relationship("Comment", back_populates="user", cascade="all, delete-orphan")


class Post(Base):
    __tablename__ = "posts"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    image_url: Mapped[Optional[str]] = mapped_column(String(512), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    author: Mapped["User"] = relationship("User", back_populates="posts")
    likes: Mapped[list["Like"]] = relationship("Like", back_populates="post", cascade="all, delete-orphan")
    comments: Mapped[list["Comment"]] = relationship("Comment", back_populates="post", cascade="all, delete-orphan")


class Like(Base):
    __tablename__ = "likes"
    __table_args__ = (UniqueConstraint("user_id", "post_id", name="uq_like_user_post"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), nullable=False, index=True)

    user: Mapped["User"] = relationship("User", back_populates="likes")
    post: Mapped["Post"] = relationship("Post", back_populates="likes")


class Comment(Base):
    __tablename__ = "comments"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    post_id: Mapped[int] = mapped_column(ForeignKey("posts.id"), nullable=False, index=True)
    content: Mapped[str] = mapped_column(Text, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=datetime.utcnow)

    user: Mapped["User"] = relationship("User", back_populates="comments")
    post: Mapped["Post"] = relationship("Post", back_populates="comments")


class Follow(Base):
    __tablename__ = "follows"
    __table_args__ = (UniqueConstraint("follower_id", "following_id", name="uq_follow_pair"),)

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    follower_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)
    following_id: Mapped[int] = mapped_column(ForeignKey("users.id"), nullable=False, index=True)


Base.metadata.create_all(bind=engine)

app = FastAPI(title="Social Media API", version="1.0.0")


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


DbSession = Annotated[Session, Depends(get_db)]


def get_user_by_username(db: Session, username: str) -> User:
    user = db.scalar(select(User).where(User.username == username))
    if not user:
        raise HTTPException(status_code=404, detail="User not found")
    return user


def get_post_or_404(db: Session, post_id: int) -> Post:
    post = db.get(Post, post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found")
    return post


class UserCreate(BaseModel):
    username: str = Field(..., min_length=1, max_length=80)
    email: str
    bio: str = ""


class UserUpdate(BaseModel):
    email: Optional[str] = None
    bio: Optional[str] = None


class UserProfile(BaseModel):
    id: int
    username: str
    email: str
    bio: str
    created_at: datetime
    follower_count: int
    following_count: int
    post_count: int

    class Config:
        from_attributes = True


class PostCreate(BaseModel):
    author_username: str
    content: str
    image_url: Optional[str] = None


class PostOut(BaseModel):
    id: int
    user_id: int
    author_username: str
    content: str
    image_url: Optional[str]
    created_at: datetime

    class Config:
        from_attributes = True


class PostDetail(PostOut):
    like_count: int
    comment_count: int


class LikeBody(BaseModel):
    username: str


class CommentCreate(BaseModel):
    username: str
    content: str


class CommentOut(BaseModel):
    id: int
    user_id: int
    username: str
    post_id: int
    content: str
    created_at: datetime

    class Config:
        from_attributes = True


class FollowBody(BaseModel):
    follower_username: str


@app.post("/api/users", response_model=UserProfile, status_code=201)
def create_user(payload: UserCreate, db: DbSession):
    if db.scalar(select(User).where(User.username == payload.username)):
        raise HTTPException(status_code=400, detail="Username already taken")
    user = User(username=payload.username, email=payload.email, bio=payload.bio or "")
    db.add(user)
    db.commit()
    db.refresh(user)
    return _user_profile(db, user)


@app.get("/api/users/{username}", response_model=UserProfile)
def get_user(username: str, db: DbSession):
    user = get_user_by_username(db, username)
    return _user_profile(db, user)


@app.put("/api/users/{username}", response_model=UserProfile)
def update_user(username: str, payload: UserUpdate, db: DbSession):
    user = get_user_by_username(db, username)
    if payload.email is not None:
        user.email = payload.email
    if payload.bio is not None:
        user.bio = payload.bio
    db.commit()
    db.refresh(user)
    return _user_profile(db, user)


def _user_profile(db: Session, user: User) -> UserProfile:
    follower_count = db.scalar(
        select(func.count()).select_from(Follow).where(Follow.following_id == user.id)
    ) or 0
    following_count = db.scalar(
        select(func.count()).select_from(Follow).where(Follow.follower_id == user.id)
    ) or 0
    post_count = db.scalar(select(func.count()).select_from(Post).where(Post.user_id == user.id)) or 0
    return UserProfile(
        id=user.id,
        username=user.username,
        email=user.email,
        bio=user.bio,
        created_at=user.created_at,
        follower_count=follower_count,
        following_count=following_count,
        post_count=post_count,
    )


@app.post("/api/posts", response_model=PostOut, status_code=201)
def create_post(payload: PostCreate, db: DbSession):
    author = get_user_by_username(db, payload.author_username)
    post = Post(user_id=author.id, content=payload.content, image_url=payload.image_url)
    db.add(post)
    db.commit()
    db.refresh(post)
    return _post_out(db, post)


@app.get("/api/posts", response_model=list[PostOut])
def list_posts(
    db: DbSession,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
    author: Optional[str] = None,
):
    q = select(Post).order_by(Post.created_at.desc())
    if author:
        u = db.scalar(select(User).where(User.username == author))
        if not u:
            return []
        q = q.where(Post.user_id == u.id)
    posts = list(db.scalars(q.offset(skip).limit(limit)).all())
    return [_post_out(db, p) for p in posts]


@app.get("/api/posts/{post_id}", response_model=PostDetail)
def get_post(post_id: int, db: DbSession):
    post = get_post_or_404(db, post_id)
    like_count = db.scalar(select(func.count()).select_from(Like).where(Like.post_id == post.id)) or 0
    comment_count = db.scalar(select(func.count()).select_from(Comment).where(Comment.post_id == post.id)) or 0
    base = _post_out(db, post)
    return PostDetail(
        **base.model_dump(),
        like_count=like_count,
        comment_count=comment_count,
    )


@app.delete("/api/posts/{post_id}", status_code=204)
def delete_post(post_id: int, db: DbSession):
    post = get_post_or_404(db, post_id)
    db.delete(post)
    db.commit()


@app.post("/api/posts/{post_id}/like", status_code=201)
def like_post(post_id: int, body: LikeBody, db: DbSession):
    post = get_post_or_404(db, post_id)
    user = get_user_by_username(db, body.username)
    existing = db.scalar(
        select(Like).where(Like.user_id == user.id, Like.post_id == post.id)
    )
    if existing:
        raise HTTPException(status_code=400, detail="Already liked")
    db.add(Like(user_id=user.id, post_id=post.id))
    db.commit()


@app.delete("/api/posts/{post_id}/like", status_code=204)
def unlike_post(post_id: int, db: DbSession, username: str = Query(...)):
    post = get_post_or_404(db, post_id)
    user = get_user_by_username(db, username)
    like = db.scalar(select(Like).where(Like.user_id == user.id, Like.post_id == post.id))
    if not like:
        raise HTTPException(status_code=404, detail="Like not found")
    db.delete(like)
    db.commit()


@app.get("/api/posts/{post_id}/likes", response_model=list[str])
def list_likers(post_id: int, db: DbSession):
    get_post_or_404(db, post_id)
    rows = db.execute(
        select(User.username)
        .join(Like, Like.user_id == User.id)
        .where(Like.post_id == post_id)
        .order_by(Like.id)
    ).all()
    return [r[0] for r in rows]


@app.post("/api/posts/{post_id}/comments", response_model=CommentOut, status_code=201)
def add_comment(post_id: int, body: CommentCreate, db: DbSession):
    post = get_post_or_404(db, post_id)
    user = get_user_by_username(db, body.username)
    c = Comment(user_id=user.id, post_id=post.id, content=body.content)
    db.add(c)
    db.commit()
    db.refresh(c)
    return _comment_out(db, c)


@app.get("/api/posts/{post_id}/comments", response_model=list[CommentOut])
def list_comments(
    post_id: int,
    db: DbSession,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    get_post_or_404(db, post_id)
    q = (
        select(Comment)
        .where(Comment.post_id == post_id)
        .order_by(Comment.created_at.asc())
        .offset(skip)
        .limit(limit)
    )
    comments = list(db.scalars(q).all())
    return [_comment_out(db, c) for c in comments]


@app.delete("/api/comments/{comment_id}", status_code=204)
def delete_comment(comment_id: int, db: DbSession):
    c = db.get(Comment, comment_id)
    if not c:
        raise HTTPException(status_code=404, detail="Comment not found")
    db.delete(c)
    db.commit()


@app.post("/api/users/{username}/follow", status_code=201)
def follow_user(username: str, body: FollowBody, db: DbSession):
    target = get_user_by_username(db, username)
    follower = get_user_by_username(db, body.follower_username)
    if follower.id == target.id:
        raise HTTPException(status_code=400, detail="Cannot follow yourself")
    if db.scalar(
        select(Follow).where(Follow.follower_id == follower.id, Follow.following_id == target.id)
    ):
        raise HTTPException(status_code=400, detail="Already following")
    db.add(Follow(follower_id=follower.id, following_id=target.id))
    db.commit()


@app.delete("/api/users/{username}/follow", status_code=204)
def unfollow_user(
    username: str,
    db: DbSession,
    follower: str = Query(..., description="follower username"),
):
    target = get_user_by_username(db, username)
    follower_user = get_user_by_username(db, follower)
    row = db.scalar(
        select(Follow).where(
            Follow.follower_id == follower_user.id,
            Follow.following_id == target.id,
        )
    )
    if not row:
        raise HTTPException(status_code=404, detail="Follow relationship not found")
    db.delete(row)
    db.commit()


@app.get("/api/users/{username}/followers", response_model=list[str])
def list_followers(username: str, db: DbSession):
    user = get_user_by_username(db, username)
    rows = db.execute(
        select(User.username)
        .join(Follow, Follow.follower_id == User.id)
        .where(Follow.following_id == user.id)
        .order_by(Follow.id)
    ).all()
    return [r[0] for r in rows]


@app.get("/api/users/{username}/following", response_model=list[str])
def list_following(username: str, db: DbSession):
    user = get_user_by_username(db, username)
    rows = db.execute(
        select(User.username)
        .join(Follow, Follow.following_id == User.id)
        .where(Follow.follower_id == user.id)
        .order_by(Follow.id)
    ).all()
    return [r[0] for r in rows]


@app.get("/api/feed/{username}", response_model=list[PostOut])
def user_feed(
    username: str,
    db: DbSession,
    skip: int = Query(0, ge=0),
    limit: int = Query(20, ge=1, le=100),
):
    user = get_user_by_username(db, username)
    following_ids_subq = select(Follow.following_id).where(Follow.follower_id == user.id)
    q = (
        select(Post)
        .where(Post.user_id.in_(following_ids_subq))
        .order_by(Post.created_at.desc())
        .offset(skip)
        .limit(limit)
    )
    posts = list(db.scalars(q).all())
    return [_post_out(db, p) for p in posts]


def _post_out(db: Session, post: Post) -> PostOut:
    author = db.get(User, post.user_id)
    uname = author.username if author else ""
    return PostOut(
        id=post.id,
        user_id=post.user_id,
        author_username=uname,
        content=post.content,
        image_url=post.image_url,
        created_at=post.created_at,
    )


def _comment_out(db: Session, c: Comment) -> CommentOut:
    u = db.get(User, c.user_id)
    return CommentOut(
        id=c.id,
        user_id=c.user_id,
        username=u.username if u else "",
        post_id=c.post_id,
        content=c.content,
        created_at=c.created_at,
    )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
