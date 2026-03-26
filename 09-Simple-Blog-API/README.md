# Simple Blog API

A minimal REST API for blog posts with categories and tags. It demonstrates a **one-to-many** relationship (each post belongs to one category; a category has many posts) and a **many-to-many** relationship (posts and tags via an association table).

## Features

- CRUD for posts with optional tags on create/update
- Categories and tags with unique names
- List posts with **pagination** (`page`, `per_page`) and **filters** (`category`, `tag` by id, `search` on title and content)
- SQLite file database (`blog.db`) created on first run
- Interactive docs at `/docs` (Swagger UI)

## Tech stack

- [FastAPI](https://fastapi.tiangolo.com/) — web framework
- [SQLAlchemy 2.0](https://www.sqlalchemy.org/) — ORM and relationships
- [SQLite](https://www.sqlite.org/) — embedded database
- [Uvicorn](https://www.uvicorn.org/) — ASGI server
- [Pydantic](https://docs.pydantic.dev/) — request/response validation

## Installation

```bash
cd 09-Simple-Blog-API
python -m venv .venv
```

Activate the virtual environment (Windows PowerShell):

```powershell
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## Run the server

```bash
uvicorn main:app --reload --port 8000
```

- API base: `http://127.0.0.1:8000`
- OpenAPI UI: `http://127.0.0.1:8000/docs`

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/categories` | Create category (`name`) |
| `GET` | `/api/categories` | List categories |
| `POST` | `/api/tags` | Create tag (`name`) |
| `GET` | `/api/tags` | List tags |
| `POST` | `/api/posts` | Create post (`title`, `content`, `category_id`, optional `tag_ids`) |
| `GET` | `/api/posts` | List posts (`page`, `per_page`, optional `category`, `tag`, `search`) |
| `GET` | `/api/posts/{id}` | Get one post with category and tags |
| `PUT` | `/api/posts/{id}` | Update post (partial fields; `tag_ids` replaces all tags) |
| `DELETE` | `/api/posts/{id}` | Delete post |

Query parameters for listing posts:

- `page` — default `1`
- `per_page` — default `10`, max `100`
- `category` — category **id**
- `tag` — tag **id**
- `search` — case-insensitive match in title and content

## Example: curl workflow

Replace IDs after each response as needed. On Windows PowerShell, use `curl.exe` (not the `curl` alias) if you want the same behavior as on Unix.

**1. Create categories**

```bash
curl -s -X POST http://127.0.0.1:8000/api/categories \
  -H "Content-Type: application/json" \
  -d '{"name": "Tutorials"}'

curl -s -X POST http://127.0.0.1:8000/api/categories \
  -H "Content-Type: application/json" \
  -d '{"name": "News"}'
```

**2. Create tags**

```bash
curl -s -X POST http://127.0.0.1:8000/api/tags \
  -H "Content-Type: application/json" \
  -d '{"name": "python"}'

curl -s -X POST http://127.0.0.1:8000/api/tags \
  -H "Content-Type: application/json" \
  -d '{"name": "fastapi"}'
```

**3. Create a post linked to a category and tags**

Assume category id `1` and tag ids `1` and `2`:

```bash
curl -s -X POST http://127.0.0.1:8000/api/posts \
  -H "Content-Type: application/json" \
  -d '{"title": "Building APIs with FastAPI", "content": "FastAPI pairs well with SQLAlchemy for quick CRUD APIs.", "category_id": 1, "tag_ids": [1, 2]}'
```

**4. List posts with pagination and filters**

```bash
curl -s "http://127.0.0.1:8000/api/posts?page=1&per_page=10"

curl -s "http://127.0.0.1:8000/api/posts?category=1&tag=1"

curl -s "http://127.0.0.1:8000/api/posts?search=FastAPI"
```

**5. Get, update, delete a post**

```bash
curl -s http://127.0.0.1:8000/api/posts/1

curl -s -X PUT http://127.0.0.1:8000/api/posts/1 \
  -H "Content-Type: application/json" \
  -d '{"title": "Updated title", "tag_ids": [1]}'

curl -s -X DELETE http://127.0.0.1:8000/api/posts/1
```

## Project structure

```
09-Simple-Blog-API/
├── main.py           # FastAPI app, models, schemas, routes
├── requirements.txt  # Python dependencies
├── README.md         # This file
└── blog.db           # SQLite database (created on first run)
```

## Data model

- **Category** — `id`, unique `name`; has many **posts**
- **Post** — `id`, `title`, `content`, `category_id`, `created_at`, `updated_at`
- **Tag** — `id`, unique `name`; linked to many **posts**
- **post_tags** — composite primary key (`post_id`, `tag_id`) linking posts and tags
