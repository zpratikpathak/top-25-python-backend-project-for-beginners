# Markdown Note-Taking API

A small REST API for creating, searching, and managing notes stored as Markdown. Each note is persisted in SQLite; the API returns both the raw Markdown and an HTML rendering for read endpoints.

## Features

- Create, list, read, update, and delete notes
- Full-text style search across title and Markdown body (`?search=`)
- Paginated listing (`?page=` and `per_page=`)
- Single-note responses include raw Markdown and rendered HTML
- Dedicated endpoint that returns only the HTML document fragment for a note

## Tech stack

- **Python 3.11+** (recommended)
- **FastAPI** — HTTP API
- **Uvicorn** — ASGI server (port **8000**)
- **SQLAlchemy** — ORM
- **SQLite** — file database (`notes.db`)
- **markdown** — Markdown → HTML

## Installation

```bash
cd 05-Markdown-Note-Taking-App
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

Run the server:

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Or:

```bash
python main.py
```

Interactive docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

## API endpoints

### `POST /api/notes`

Create a note. Send JSON with `title` and `content` (Markdown string).

```bash
curl -s -X POST http://127.0.0.1:8000/api/notes ^
  -H "Content-Type: application/json" ^
  -d "{\"title\": \"Hello\", \"content\": \"# Title\n\nSome **bold** text and a [link](https://example.com).\"}"
```

Response (JSON): includes `id`, `title`, `content` (raw Markdown), `html` (rendered), `created_at`, `updated_at`.

### `GET /api/notes`

List notes. Optional query parameters: `search`, `page` (default `1`), `per_page` (default `10`, max `100`).

```bash
curl -s "http://127.0.0.1:8000/api/notes?page=1&per_page=10&search=bold"
```

### `GET /api/notes/{id}`

Get one note with Markdown and HTML.

```bash
curl -s http://127.0.0.1:8000/api/notes/1
```

Example fragment of the response (pretty-print for clarity):

```json
{
  "id": 1,
  "title": "Hello",
  "content": "# Title\n\nSome **bold** text and a [link](https://example.com).",
  "html": "<h1>Title</h1>\n<p>Some <strong>bold</strong> text and a <a href=\"https://example.com\">link</a>.</p>",
  "created_at": "...",
  "updated_at": "..."
}
```

### `PUT /api/notes/{id}`

Update `title` and/or `content`. At least one field must be provided.

```bash
curl -s -X PUT http://127.0.0.1:8000/api/notes/1 ^
  -H "Content-Type: application/json" ^
  -d "{\"content\": \"## Updated\n\n- item one\n- item two\"}"
```

### `DELETE /api/notes/{id}`

```bash
curl -s -i -X DELETE http://127.0.0.1:8000/api/notes/1
```

Returns HTTP **204** with an empty body on success (the `-i` flag prints response headers so you can see the status code).

### `GET /api/notes/{id}/html`

Returns **only** the rendered HTML as `text/html` (not a JSON wrapper).

```bash
curl -s http://127.0.0.1:8000/api/notes/1/html
```

Example body:

```html
<h1>Title</h1>
<p>Some <strong>bold</strong> text and a <a href="https://example.com">link</a>.</p>
```

On Unix/macOS, use single quotes for JSON in `curl` and omit the `^` line continuations.

## Project structure

```text
05-Markdown-Note-Taking-App/
├── main.py           # FastAPI app, models, routes
├── requirements.txt  # Python dependencies
├── README.md         # This file
└── notes.db          # Created automatically on first run (SQLite)
```
