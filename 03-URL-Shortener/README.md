# URL Shortener

A small REST API that turns long URLs into short codes, tracks clicks, and serves redirects. Built with FastAPI and SQLite.

## Features

- Shorten URLs with automatic base62-style codes derived from a SHA-256 hash
- Temporary redirect (307) to the original URL with per-link click counting
- Per-link statistics and a list of all shortened URLs
- Delete shortened links by code
- URL validation on create (HTTP/HTTPS via Pydantic)

## Tech stack

- Python 3.10+
- FastAPI
- Uvicorn
- SQLAlchemy 2.x
- SQLite (`urls.db` in the project directory)

## Installation

```bash
cd 03-URL-Shortener
python -m venv .venv
```

Activate the virtual environment:

- Windows (PowerShell): `.venv\Scripts\Activate.ps1`
- macOS/Linux: `source .venv/bin/activate`

Install dependencies:

```bash
pip install -r requirements.txt
```

Run the server (reload for development):

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Open interactive docs at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

## API

Base URL: `http://127.0.0.1:8000`

### POST `/api/shorten`

Create a short link. Body must be JSON with a valid `http` or `https` URL.

```bash
curl -X POST http://127.0.0.1:8000/api/shorten ^
  -H "Content-Type: application/json" ^
  -d "{\"url\": \"https://example.com/path\"}"
```

PowerShell (single line):

```powershell
curl.exe -X POST http://127.0.0.1:8000/api/shorten -H "Content-Type: application/json" -d "{\"url\": \"https://example.com/path\"}"
```

### GET `/{short_code}`

Redirect to the stored URL (307) and increment the click counter.

```bash
curl -v http://127.0.0.1:8000/YOUR_CODE
```

### GET `/api/urls/{short_code}/stats`

Return `original_url`, `short_code`, `click_count`, and `created_at`.

```bash
curl http://127.0.0.1:8000/api/urls/YOUR_CODE/stats
```

### GET `/api/urls`

List all shortened URLs with the same fields as stats, newest first.

```bash
curl http://127.0.0.1:8000/api/urls
```

### DELETE `/api/urls/{short_code}`

Remove a shortened URL. Returns `204` with no body on success.

```bash
curl -X DELETE http://127.0.0.1:8000/api/urls/YOUR_CODE
```

## Project structure

```
03-URL-Shortener/
├── main.py           # FastAPI app, models, routes
├── requirements.txt  # Python dependencies
├── README.md         # This file
└── urls.db           # SQLite database (created on first run)
```
