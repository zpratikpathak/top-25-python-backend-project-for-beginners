# Random Quote API

A small REST API built with Flask and SQLite. It serves famous quotes with filtering, pagination, and CRUD operations for quotes.

## Features

- Fetch a random quote, optionally filtered by category
- List quotes with optional filters for author and category
- Paginated listing with configurable page size
- Retrieve, create, and delete quotes by ID
- SQLite database created automatically on startup
- ~20 seeded quotes across inspirational, life, wisdom, humor, and success categories

## Tech stack

- Python 3
- Flask
- SQLite (`sqlite3` standard library)

## Installation

1. Create and activate a virtual environment (recommended):

   ```bash
   python -m venv .venv
   .venv\Scripts\activate
   ```

2. Install dependencies:

   ```bash
   pip install -r requirements.txt
   ```

3. Run the server (listens on port 5000):

   ```bash
   python main.py
   ```

   Or:

   ```bash
   set FLASK_APP=main.py
   flask run --host 0.0.0.0 --port 5000
   ```

   On first run, `quotes.db` is created next to `main.py` and seeded if empty.

## API endpoints

Base URL: `http://localhost:5000`

### GET `/api/quotes/random`

Returns one random quote. Optional query: `category` (case-insensitive match).

```bash
curl http://localhost:5000/api/quotes/random
curl "http://localhost:5000/api/quotes/random?category=humor"
```

### GET `/api/quotes`

Lists quotes. Query parameters:

- `author` — substring match on author name
- `category` — exact category (case-insensitive)
- `page` — page number (default `1`)
- `per_page` — page size (default `20`, max `100`)

```bash
curl http://localhost:5000/api/quotes
curl "http://localhost:5000/api/quotes?author=Oscar"
curl "http://localhost:5000/api/quotes?category=wisdom&page=1&per_page=5"
```

### GET `/api/quotes/:id`

Returns a single quote by numeric ID.

```bash
curl http://localhost:5000/api/quotes/1
```

### POST `/api/quotes`

Creates a quote. JSON body must include `text`, `author`, and `category`.

```bash
curl -X POST http://localhost:5000/api/quotes ^
  -H "Content-Type: application/json" ^
  -d "{\"text\": \"Your quote here.\", \"author\": \"You\", \"category\": \"life\"}"
```

On Unix/macOS, use single quotes for the JSON:

```bash
curl -X POST http://localhost:5000/api/quotes \
  -H "Content-Type: application/json" \
  -d '{"text": "Your quote here.", "author": "You", "category": "life"}'
```

### DELETE `/api/quotes/:id`

Deletes a quote by ID. Returns `204 No Content` on success.

```bash
curl -X DELETE http://localhost:5000/api/quotes/1
```

## Project structure

```
02-Random-Quote-API/
├── main.py           # Flask app, routes, DB setup, seed data
├── requirements.txt  # Python dependencies
├── README.md         # This file
└── quotes.db         # SQLite database (created on first run)
```
