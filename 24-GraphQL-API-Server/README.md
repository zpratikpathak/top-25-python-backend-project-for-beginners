# Library GraphQL API Server

A small **library management** backend: authors, books, genres, and relationships, exposed through **GraphQL** on FastAPI with **Strawberry**, **SQLAlchemy 2** (async), and **SQLite**.

## Features

- **GraphQL schema** for authors, books, and genres (including relations).
- **Queries** with optional filters and **pagination** for books and authors.
- **Mutations** for creating, updating, and deleting authors and books, plus creating genres.
- **`bookAdded` subscription** that pushes each newly created book to subscribers (in-process fan-out).
- **DataLoader** batching on the GraphQL context to reduce N+1 queries when resolving nested fields (e.g. many books with authors and genres).
- **GraphiQL** served at `/graphql` for interactive queries, mutations, and subscriptions over WebSocket.

## Tech stack

| Layer        | Choice                                      |
|-------------|----------------------------------------------|
| HTTP / ASGI | FastAPI, Uvicorn                             |
| GraphQL     | Strawberry (`strawberry-graphql[fastapi]`)   |
| Database    | SQLite via `aiosqlite`, SQLAlchemy 2 asyncio |

## GraphQL schema overview

Strawberry maps Python `snake_case` fields to **camelCase** in the GraphQL schema (e.g. `published_year` → `publishedYear`).

### Types

- **Author** — `id`, `name`, `bio`, `books`
- **Book** — `id`, `title`, `isbn`, `publishedYear`, `author`, `genres`
- **Genre** — `id`, `name`, `books`
- **BookPage** / **AuthorPage** — `items`, `total`, `page`, `perPage`

### Queries

- `books(search, authorId, genreId, page, perPage)` → `BookPage`
- `book(id)` → `Book` or `null`
- `authors(search, page, perPage)` → `AuthorPage`
- `author(id)` → `Author` or `null`
- `genres` → `[Genre!]!`

### Mutations

- `createAuthor(name, bio)` → `Author`
- `updateAuthor(id, name, bio)` → `Author` or `null`
- `deleteAuthor(id)` → `Boolean!`
- `createBook(title, isbn, publishedYear, authorId, genreIds)` → `Book` or `null` (invalid `authorId`)
- `updateBook(id, title, isbn, publishedYear, authorId, genreIds)` → `Book` or `null`
- `deleteBook(id)` → `Boolean!`
- `createGenre(name)` → `Genre`

### Subscriptions

- `bookAdded` → stream of `Book` for each successful `createBook`

## Installation

```bash
cd 24-GraphQL-API-Server
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run the server

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Or:

```bash
python main.py
```

The app listens on **port 8000** by default when using `python main.py` (with reload).

On first startup, SQLite creates `library.db` in the project directory and seeds a few authors, genres, and books if the database is empty.

## Using the GraphQL IDE (GraphiQL)

1. Start the server.
2. Open a browser to **http://localhost:8000/graphql** (or your host/port).
3. Use the editor for **query**, **mutation**, and **subscription** (subscriptions use the router’s WebSocket protocols).

## Example queries and mutations

**List books with author and genres (paginated):**

```graphql
query {
  books(page: 1, perPage: 10, search: "Darkness") {
    total
    page
    perPage
    items {
      id
      title
      publishedYear
      author {
        name
        bio
      }
      genres {
        name
      }
    }
  }
}
```

**Single book and all genres:**

```graphql
query {
  book(id: 1) {
    id
    title
    isbn
  }
  genres {
    id
    name
  }
}
```

**Authors with their books:**

```graphql
query {
  authors(page: 1, perPage: 5) {
    total
    items {
      id
      name
      books {
        title
      }
    }
  }
}
```

**Create author and book:**

```graphql
mutation {
  createAuthor(name: "New Author", bio: "Writes fiction") {
    id
    name
  }
}

mutation {
  createBook(
    title: "Example Novel"
    isbn: "9781234567890"
    publishedYear: 2024
    authorId: 1
    genreIds: [1, 2]
  ) {
    id
    title
    author {
      name
    }
    genres {
      name
    }
  }
}
```

**Update and delete:**

```graphql
mutation {
  updateAuthor(id: 1, name: "Updated Name", bio: "New bio") {
    id
    name
  }
}

mutation {
  deleteBook(id: 99)
}
```

**Subscribe to new books (run in GraphiQL Subscriptions panel):**

```graphql
subscription {
  bookAdded {
    id
    title
    publishedYear
    author {
      name
    }
    genres {
      name
    }
  }
}
```

Then run a `createBook` mutation in another tab or client; each create emits one event on `bookAdded`.

## Project structure

```
24-GraphQL-API-Server/
├── main.py              # FastAPI app, SQLAlchemy models, Strawberry schema, DataLoaders
├── requirements.txt     # Python dependencies
├── README.md            # This file
└── library.db           # SQLite file (created at runtime)
```

## DataLoaders

The GraphQL context builds per-request **DataLoader** instances that batch:

- Authors, books, and genres by primary key  
- Books per author  
- Genre IDs per book  
- Book IDs per genre  

Resolvers on `Book`, `Author`, and `Genre` use these loaders so a single query with many nested objects does not trigger one query per row.
