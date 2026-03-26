# Todo API

A small **REST API** for managing todo items backed by **SQLite**. It is a learning-oriented project: you can see how to structure a Flask app, use **raw SQL** with the standard library `sqlite3` module, return consistent **JSON** responses, and apply common **HTTP status codes** and error handling patterns.

**Concepts covered:** REST design, CRUD operations, query-string filtering, request validation, per-request database connections with Flask’s application context, and SQLite schema basics (timestamps stored as ISO 8601 UTC strings).

## Features

- List, create, read, update, and delete todos
- Filter todos by completion status (`?completed=true` or `?completed=false`)
- Toggle completion with a dedicated endpoint
- Automatic database and table creation on startup
- JSON request/response bodies with clear error messages

## Tech stack

- **Python 3**
- **Flask** — HTTP routing and JSON helpers
- **SQLite** — persistence via `sqlite3` (no ORM)

## Installation

1. **Create and activate a virtual environment** (from the project directory):

   ```bash
   python -m venv .venv
   ```

   **Windows (PowerShell):**

   ```powershell
   .\.venv\Scripts\Activate.ps1
   ```

   **macOS / Linux:**

   ```bash
   source .venv/bin/activate
   ```

2. **Install dependencies:**

   ```bash
   pip install -r requirements.txt
   ```

3. **Run the server** (listens on `http://0.0.0.0:5000`; open `http://127.0.0.1:5000` locally):

   ```bash
   python main.py
   ```

   On first run, `todos.db` is created in the project folder.

## API reference

All successful todo responses use this JSON shape:

```json
{
  "id": 1,
  "title": "Buy milk",
  "description": "2% organic",
  "completed": false,
  "created_at": "2025-03-26T12:00:00+00:00",
  "updated_at": "2025-03-26T12:00:00+00:00"
}
```

### `GET /api/todos`

List all todos, optionally filtered.

**Query parameters**

| Name        | Description                                      |
|------------|---------------------------------------------------|
| `completed` | Optional. `true` or `false` (lowercase). Invalid values return `400`. |

**Example — all todos**

```bash
curl -s http://127.0.0.1:5000/api/todos
```

**Example — only completed**

```bash
curl -s "http://127.0.0.1:5000/api/todos?completed=true"
```

**Example — only incomplete**

```bash
curl -s "http://127.0.0.1:5000/api/todos?completed=false"
```

### `GET /api/todos/:id`

Return one todo. `404` if missing.

```bash
curl -s http://127.0.0.1:5000/api/todos/1
```

**404 example**

```bash
curl -s -i http://127.0.0.1:5000/api/todos/999
```

### `POST /api/todos`

Create a todo. **`title`** is required (non-empty string). **`description`** is optional (defaults to `""`).

**Request body**

```json
{
  "title": "Learn Flask",
  "description": "Build a Todo API"
}
```

```bash
curl -s -X POST http://127.0.0.1:5000/api/todos \
  -H "Content-Type: application/json" \
  -d "{\"title\":\"Learn Flask\",\"description\":\"Build a Todo API\"}"
```

**Response:** `201 Created` with the new todo JSON.

### `PUT /api/todos/:id`

Replace/update fields. Omitted keys keep existing values. **`title`** must remain a non-empty string; **`description`** must be a string if sent; **`completed`** must be a boolean if sent.

**Request body**

```json
{
  "title": "Learn Flask",
  "description": "Done with the README",
  "completed": true
}
```

```bash
curl -s -X PUT http://127.0.0.1:5000/api/todos/1 \
  -H "Content-Type: application/json" \
  -d "{\"title\":\"Learn Flask\",\"description\":\"Done with the README\",\"completed\":true}"
```

**Response:** `200 OK` with the updated todo. `404` if the id does not exist.

### `DELETE /api/todos/:id`

Delete a todo. **Response:** `204 No Content` on success; `404` if not found.

```bash
curl -s -i -X DELETE http://127.0.0.1:5000/api/todos/1
```

### `PATCH /api/todos/:id/toggle`

Flip `completed` between `true` and `false` and refresh `updated_at`.

```bash
curl -s -X PATCH http://127.0.0.1:5000/api/todos/1/toggle
```

**Response:** `200 OK` with the updated todo. `404` if the id does not exist.

### Common errors

| Status | Meaning |
|--------|---------|
| `400`  | Bad request (validation, invalid `completed` query) |
| `404`  | Todo not found |
| `415`  | `Content-Type` is not JSON (for POST/PUT) |

Error body shape:

```json
{
  "error": "Human-readable message."
}
```

## Project structure

```
01-Todo-API/
├── main.py           # Flask app, routes, SQLite access
├── requirements.txt  # Python dependencies
├── README.md         # This file
└── todos.db          # SQLite database (created on first run)
```
