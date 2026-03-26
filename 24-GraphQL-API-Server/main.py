from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from typing import Annotated, AsyncGenerator, Optional

from fastapi import Depends, FastAPI
from sqlalchemy import Column, ForeignKey, Integer, String, Table, Text, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship, selectinload

import strawberry
from strawberry.dataloader import DataLoader
from strawberry.fastapi import BaseContext, GraphQLRouter


DATABASE_URL = "sqlite+aiosqlite:///./library.db"

engine = create_async_engine(DATABASE_URL, echo=False)
AsyncSessionLocal = async_sessionmaker(engine, expire_on_commit=False, class_=AsyncSession)


class Base(DeclarativeBase):
    pass


book_genre = Table(
    "book_genre",
    Base.metadata,
    Column("book_id", ForeignKey("books.id", ondelete="CASCADE"), primary_key=True),
    Column("genre_id", ForeignKey("genres.id", ondelete="CASCADE"), primary_key=True),
)


class AuthorModel(Base):
    __tablename__ = "authors"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    bio: Mapped[Optional[str]] = mapped_column(Text, nullable=True)
    books: Mapped[list["BookModel"]] = relationship(
        back_populates="author",
        cascade="all, delete-orphan",
    )


class GenreModel(Base):
    __tablename__ = "genres"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(String(120), unique=True, nullable=False)
    books: Mapped[list["BookModel"]] = relationship(
        secondary=book_genre,
        back_populates="genres",
    )


class BookModel(Base):
    __tablename__ = "books"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    title: Mapped[str] = mapped_column(String(500), nullable=False)
    isbn: Mapped[str] = mapped_column(String(32), unique=True, nullable=False)
    published_year: Mapped[int] = mapped_column(Integer, nullable=False)
    author_id: Mapped[int] = mapped_column(ForeignKey("authors.id", ondelete="CASCADE"))
    author: Mapped[AuthorModel] = relationship(back_populates="books")
    genres: Mapped[list[GenreModel]] = relationship(
        secondary=book_genre,
        back_populates="books",
    )


_book_subscriber_queues: list[asyncio.Queue[int]] = []
_subscribers_lock = asyncio.Lock()


async def publish_book_added(book_id: int) -> None:
    async with _subscribers_lock:
        queues = list(_book_subscriber_queues)
    for q in queues:
        await q.put(book_id)


def dataloader_clear(loader: DataLoader, key: int) -> None:
    try:
        loader.clear(key)
    except KeyError:
        pass


class GraphQLContext(BaseContext):
    def __init__(self, db: AsyncSession) -> None:
        super().__init__()
        self.db = db

        async def load_authors(keys: list[int]) -> list[Optional[AuthorModel]]:
            if not keys:
                return []
            res = await self.db.execute(select(AuthorModel).where(AuthorModel.id.in_(keys)))
            rows = {a.id: a for a in res.scalars().all()}
            return [rows.get(k) for k in keys]

        async def load_books(keys: list[int]) -> list[Optional[BookModel]]:
            if not keys:
                return []
            res = await self.db.execute(select(BookModel).where(BookModel.id.in_(keys)))
            rows = {b.id: b for b in res.scalars().all()}
            return [rows.get(k) for k in keys]

        async def load_genres(keys: list[int]) -> list[Optional[GenreModel]]:
            if not keys:
                return []
            res = await self.db.execute(select(GenreModel).where(GenreModel.id.in_(keys)))
            rows = {g.id: g for g in res.scalars().all()}
            return [rows.get(k) for k in keys]

        async def load_books_for_authors(keys: list[int]) -> list[list[BookModel]]:
            if not keys:
                return []
            res = await self.db.execute(select(BookModel).where(BookModel.author_id.in_(keys)))
            by_author: dict[int, list[BookModel]] = {k: [] for k in keys}
            for b in res.scalars().all():
                if b.author_id in by_author:
                    by_author[b.author_id].append(b)
            return [by_author[k] for k in keys]

        async def load_genre_ids_for_books(keys: list[int]) -> list[list[int]]:
            if not keys:
                return []
            stmt = select(book_genre.c.book_id, book_genre.c.genre_id).where(
                book_genre.c.book_id.in_(keys)
            )
            res = await self.db.execute(stmt)
            by_book: dict[int, list[int]] = {k: [] for k in keys}
            for book_id, genre_id in res.all():
                if book_id in by_book:
                    by_book[book_id].append(genre_id)
            return [by_book[k] for k in keys]

        async def load_book_ids_for_genres(keys: list[int]) -> list[list[int]]:
            if not keys:
                return []
            stmt = select(book_genre.c.genre_id, book_genre.c.book_id).where(
                book_genre.c.genre_id.in_(keys)
            )
            res = await self.db.execute(stmt)
            by_genre: dict[int, list[int]] = {k: [] for k in keys}
            for genre_id, book_id in res.all():
                if genre_id in by_genre:
                    by_genre[genre_id].append(book_id)
            return [by_genre[k] for k in keys]

        self.author_loader = DataLoader(load_fn=load_authors)
        self.book_loader = DataLoader(load_fn=load_books)
        self.genre_loader = DataLoader(load_fn=load_genres)
        self.books_for_author_loader = DataLoader(load_fn=load_books_for_authors)
        self.genre_ids_for_book_loader = DataLoader(load_fn=load_genre_ids_for_books)
        self.book_ids_for_genre_loader = DataLoader(load_fn=load_book_ids_for_genres)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    async with AsyncSessionLocal() as session:
        yield session


def book_filters(
    search: Optional[str],
    author_id: Optional[int],
    genre_id: Optional[int],
):
    def apply(stmt):
        if search:
            stmt = stmt.where(BookModel.title.contains(search))
        if author_id is not None:
            stmt = stmt.where(BookModel.author_id == author_id)
        if genre_id is not None:
            stmt = stmt.join(book_genre, book_genre.c.book_id == BookModel.id).where(
                book_genre.c.genre_id == genre_id
            )
        return stmt

    return apply


def books_select_ids(search: Optional[str], author_id: Optional[int], genre_id: Optional[int]):
    stmt = select(BookModel.id)
    stmt = book_filters(search, author_id, genre_id)(stmt)
    if genre_id is not None:
        stmt = stmt.distinct()
    return stmt


def books_select_models(search: Optional[str], author_id: Optional[int], genre_id: Optional[int]):
    stmt = select(BookModel)
    stmt = book_filters(search, author_id, genre_id)(stmt)
    if genre_id is not None:
        stmt = stmt.distinct()
    return stmt


@strawberry.type
class Genre:
    id: int
    name: str

    @classmethod
    def from_model(cls, g: GenreModel) -> Genre:
        return cls(id=g.id, name=g.name)

    @strawberry.field
    async def books(self, info: strawberry.Info) -> list[Book]:
        ctx: GraphQLContext = info.context
        book_ids = await ctx.book_ids_for_genre_loader.load(self.id)
        orms = await ctx.book_loader.load_many(book_ids)
        return [Book.from_model(b) for b in orms if b is not None]


@strawberry.type
class Author:
    id: int
    name: str
    bio: Optional[str]

    @classmethod
    def from_model(cls, a: AuthorModel) -> Author:
        return cls(id=a.id, name=a.name, bio=a.bio)

    @strawberry.field
    async def books(self, info: strawberry.Info) -> list[Book]:
        ctx: GraphQLContext = info.context
        orms = await ctx.books_for_author_loader.load(self.id)
        return [Book.from_model(b) for b in orms]


@strawberry.type
class Book:
    id: int
    title: str
    isbn: str
    published_year: int
    author_id: strawberry.Private[int]

    @classmethod
    def from_model(cls, b: BookModel) -> Book:
        return cls(
            id=b.id,
            title=b.title,
            isbn=b.isbn,
            published_year=b.published_year,
            author_id=b.author_id,
        )

    @strawberry.field
    async def author(self, info: strawberry.Info) -> Optional[Author]:
        ctx: GraphQLContext = info.context
        a = await ctx.author_loader.load(self.author_id)
        return Author.from_model(a) if a else None

    @strawberry.field
    async def genres(self, info: strawberry.Info) -> list[Genre]:
        ctx: GraphQLContext = info.context
        gids = await ctx.genre_ids_for_book_loader.load(self.id)
        orms = await ctx.genre_loader.load_many(gids)
        return [Genre.from_model(g) for g in orms if g is not None]


@strawberry.type
class BookPage:
    items: list[Book]
    total: int
    page: int
    per_page: int


@strawberry.type
class AuthorPage:
    items: list[Author]
    total: int
    page: int
    per_page: int


@strawberry.type
class Query:
    @strawberry.field
    async def books(
        self,
        info: strawberry.Info,
        search: Optional[str] = None,
        author_id: Optional[int] = None,
        genre_id: Optional[int] = None,
        page: int = 1,
        per_page: int = 10,
    ) -> BookPage:
        ctx: GraphQLContext = info.context
        db = ctx.db
        if page < 1:
            page = 1
        if per_page < 1:
            per_page = 10
        if per_page > 100:
            per_page = 100

        ids_subq = books_select_ids(search, author_id, genre_id).subquery()
        count_stmt = select(func.count()).select_from(ids_subq)
        total = (await db.execute(count_stmt)).scalar_one()

        stmt = books_select_models(search, author_id, genre_id)
        stmt = stmt.order_by(BookModel.id).offset((page - 1) * per_page).limit(per_page)
        res = await db.execute(stmt)
        rows = res.scalars().unique().all()
        return BookPage(
            items=[Book.from_model(b) for b in rows],
            total=total,
            page=page,
            per_page=per_page,
        )

    @strawberry.field
    async def book(self, info: strawberry.Info, id: int) -> Optional[Book]:
        ctx: GraphQLContext = info.context
        b = await ctx.book_loader.load(id)
        return Book.from_model(b) if b else None

    @strawberry.field
    async def authors(
        self,
        info: strawberry.Info,
        search: Optional[str] = None,
        page: int = 1,
        per_page: int = 10,
    ) -> AuthorPage:
        ctx: GraphQLContext = info.context
        db = ctx.db
        if page < 1:
            page = 1
        if per_page < 1:
            per_page = 10
        if per_page > 100:
            per_page = 100

        base = select(AuthorModel)
        if search:
            base = base.where(AuthorModel.name.contains(search))
        count_stmt = select(func.count()).select_from(base.subquery())
        total = (await db.execute(count_stmt)).scalar_one()

        stmt = select(AuthorModel)
        if search:
            stmt = stmt.where(AuthorModel.name.contains(search))
        stmt = stmt.order_by(AuthorModel.id).offset((page - 1) * per_page).limit(per_page)
        res = await db.execute(stmt)
        rows = res.scalars().all()
        return AuthorPage(
            items=[Author.from_model(a) for a in rows],
            total=total,
            page=page,
            per_page=per_page,
        )

    @strawberry.field
    async def author(self, info: strawberry.Info, id: int) -> Optional[Author]:
        ctx: GraphQLContext = info.context
        a = await ctx.author_loader.load(id)
        return Author.from_model(a) if a else None

    @strawberry.field
    async def genres(self, info: strawberry.Info) -> list[Genre]:
        ctx: GraphQLContext = info.context
        res = await ctx.db.execute(select(GenreModel).order_by(GenreModel.id))
        return [Genre.from_model(g) for g in res.scalars().all()]


@strawberry.type
class Mutation:
    @strawberry.mutation
    async def create_author(
        self, info: strawberry.Info, name: str, bio: Optional[str] = None
    ) -> Author:
        ctx: GraphQLContext = info.context
        db = ctx.db
        author = AuthorModel(name=name, bio=bio)
        db.add(author)
        await db.commit()
        await db.refresh(author)
        dataloader_clear(ctx.author_loader, author.id)
        return Author.from_model(author)

    @strawberry.mutation
    async def update_author(
        self,
        info: strawberry.Info,
        id: int,
        name: Optional[str] = None,
        bio: Optional[str] = None,
    ) -> Optional[Author]:
        ctx: GraphQLContext = info.context
        db = ctx.db
        author = await db.get(AuthorModel, id)
        if not author:
            return None
        if name is not None:
            author.name = name
        if bio is not None:
            author.bio = bio
        await db.commit()
        await db.refresh(author)
        dataloader_clear(ctx.author_loader, id)
        dataloader_clear(ctx.books_for_author_loader, id)
        return Author.from_model(author)

    @strawberry.mutation
    async def delete_author(self, info: strawberry.Info, id: int) -> bool:
        ctx: GraphQLContext = info.context
        db = ctx.db
        author = await db.get(AuthorModel, id)
        if not author:
            return False
        await db.delete(author)
        await db.commit()
        dataloader_clear(ctx.author_loader, id)
        dataloader_clear(ctx.books_for_author_loader, id)
        return True

    @strawberry.mutation
    async def create_book(
        self,
        info: strawberry.Info,
        title: str,
        isbn: str,
        published_year: int,
        author_id: int,
        genre_ids: list[int],
    ) -> Optional[Book]:
        ctx: GraphQLContext = info.context
        db = ctx.db
        author = await db.get(AuthorModel, author_id)
        if not author:
            return None
        book = BookModel(
            title=title,
            isbn=isbn,
            published_year=published_year,
            author_id=author_id,
        )
        if genre_ids:
            res = await db.execute(select(GenreModel).where(GenreModel.id.in_(genre_ids)))
            book.genres.extend(res.scalars().all())
        db.add(book)
        await db.commit()
        await db.refresh(book)
        dataloader_clear(ctx.book_loader, book.id)
        dataloader_clear(ctx.books_for_author_loader, author_id)
        for gid in genre_ids:
            dataloader_clear(ctx.book_ids_for_genre_loader, gid)
        await publish_book_added(book.id)
        return Book.from_model(book)

    @strawberry.mutation
    async def update_book(
        self,
        info: strawberry.Info,
        id: int,
        title: Optional[str] = None,
        isbn: Optional[str] = None,
        published_year: Optional[int] = None,
        author_id: Optional[int] = None,
        genre_ids: Optional[list[int]] = None,
    ) -> Optional[Book]:
        ctx: GraphQLContext = info.context
        db = ctx.db
        book = await db.get(BookModel, id, options=(selectinload(BookModel.genres),))
        if not book:
            return None
        old_author_id = book.author_id
        old_genre_ids = [g.id for g in book.genres]
        if author_id is not None:
            a = await db.get(AuthorModel, author_id)
            if not a:
                return None
        if title is not None:
            book.title = title
        if isbn is not None:
            book.isbn = isbn
        if published_year is not None:
            book.published_year = published_year
        if author_id is not None:
            book.author_id = author_id
        if genre_ids is not None:
            book.genres.clear()
            if genre_ids:
                res = await db.execute(select(GenreModel).where(GenreModel.id.in_(genre_ids)))
                book.genres.extend(res.scalars().all())
        await db.commit()
        await db.refresh(book)
        dataloader_clear(ctx.book_loader, id)
        dataloader_clear(ctx.genre_ids_for_book_loader, id)
        dataloader_clear(ctx.books_for_author_loader, old_author_id)
        dataloader_clear(ctx.books_for_author_loader, book.author_id)
        for gid in set(old_genre_ids) | set(genre_ids or []):
            dataloader_clear(ctx.book_ids_for_genre_loader, gid)
        return Book.from_model(book)

    @strawberry.mutation
    async def delete_book(self, info: strawberry.Info, id: int) -> bool:
        ctx: GraphQLContext = info.context
        db = ctx.db
        book = await db.get(BookModel, id, options=(selectinload(BookModel.genres),))
        if not book:
            return False
        aid = book.author_id
        gids = [g.id for g in book.genres]
        await db.delete(book)
        await db.commit()
        dataloader_clear(ctx.book_loader, id)
        dataloader_clear(ctx.genre_ids_for_book_loader, id)
        dataloader_clear(ctx.books_for_author_loader, aid)
        for gid in gids:
            dataloader_clear(ctx.book_ids_for_genre_loader, gid)
        return True

    @strawberry.mutation
    async def create_genre(self, info: strawberry.Info, name: str) -> Genre:
        ctx: GraphQLContext = info.context
        db = ctx.db
        genre = GenreModel(name=name)
        db.add(genre)
        await db.commit()
        await db.refresh(genre)
        return Genre.from_model(genre)


@strawberry.type
class Subscription:
    @strawberry.subscription
    async def book_added(self, info: strawberry.Info) -> AsyncGenerator[Book, None]:
        queue: asyncio.Queue[int] = asyncio.Queue()
        async with _subscribers_lock:
            _book_subscriber_queues.append(queue)
        try:
            while True:
                book_id = await queue.get()
                ctx: GraphQLContext = info.context
                b = await ctx.book_loader.load(book_id)
                if b:
                    yield Book.from_model(b)
        finally:
            async with _subscribers_lock:
                if queue in _book_subscriber_queues:
                    _book_subscriber_queues.remove(queue)


schema = strawberry.Schema(query=Query, mutation=Mutation, subscription=Subscription)


async def get_context(
    db: Annotated[AsyncSession, Depends(get_db)],
) -> GraphQLContext:
    return GraphQLContext(db)


graphql_app = GraphQLRouter(schema, context_getter=get_context)


async def seed_data(session: AsyncSession) -> None:
    n = (await session.execute(select(func.count()).select_from(GenreModel))).scalar_one()
    if n > 0:
        return
    fiction = GenreModel(name="Fiction")
    scifi = GenreModel(name="Science Fiction")
    fantasy = GenreModel(name="Fantasy")
    session.add_all([fiction, scifi, fantasy])
    await session.flush()
    a1 = AuthorModel(name="Ada Lovelace", bio="Mathematician and writer.")
    a2 = AuthorModel(name="Ursula K. Le Guin", bio="Author of Earthsea and Hainish cycles.")
    session.add_all([a1, a2])
    await session.flush()
    b1 = BookModel(
        title="Notes on the Analytical Engine",
        isbn="9780000000001",
        published_year=1844,
        author_id=a1.id,
        genres=[fiction],
    )
    b2 = BookModel(
        title="The Left Hand of Darkness",
        isbn="9780441478125",
        published_year=1969,
        author_id=a2.id,
        genres=[scifi, fantasy],
    )
    session.add_all([b1, b2])
    await session.commit()


@asynccontextmanager
async def lifespan(app: FastAPI):
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    async with AsyncSessionLocal() as s:
        await seed_data(s)
    yield
    await engine.dispose()


app = FastAPI(title="Library GraphQL API", lifespan=lifespan)
app.include_router(graphql_app, prefix="/graphql")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=True)
