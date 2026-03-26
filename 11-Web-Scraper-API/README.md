# Web Scraper API

A small **FastAPI** service that fetches HTML over HTTP, parses it with **BeautifulSoup** (lxml), and returns structured extracts. Each request is recorded in **SQLite** via **SQLAlchemy** for audit and debugging.

## Features

- Custom CSS selector scraping (`POST /api/scrape`)
- Full visible text extraction (scripts/styles removed)
- Links with `href` and anchor text
- Image URLs from `src` and `srcset`
- Metadata: document title, meta description, Open Graph tags, favicon
- Scrape history with pagination
- Async HTTP via **httpx**, browser-like **User-Agent**, timeouts, and clear error responses

## Tech stack

- Python 3.10+
- FastAPI, Uvicorn
- httpx (async client)
- BeautifulSoup4 + lxml
- SQLAlchemy 2.x + SQLite

## Installation

```bash
cd 11-Web-Scraper-API
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run the server

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Interactive docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

## API endpoints

### `POST /api/scrape`

Body: `url`, `selectors` (map of label → CSS selector). Each label maps to a list of strings: element text, or resolved `href` / `src` when text is empty.

```bash
curl -s -X POST "http://127.0.0.1:8000/api/scrape" ^
  -H "Content-Type: application/json" ^
  -d "{\"url\": \"https://example.com\", \"selectors\": {\"title\": \"h1\", \"links\": \"a\"}}"
```

### `POST /api/scrape/text`

```bash
curl -s -X POST "http://127.0.0.1:8000/api/scrape/text" ^
  -H "Content-Type: application/json" ^
  -d "{\"url\": \"https://example.com\"}"
```

### `POST /api/scrape/links`

```bash
curl -s -X POST "http://127.0.0.1:8000/api/scrape/links" ^
  -H "Content-Type: application/json" ^
  -d "{\"url\": \"https://example.com\"}"
```

### `POST /api/scrape/images`

```bash
curl -s -X POST "http://127.0.0.1:8000/api/scrape/images" ^
  -H "Content-Type: application/json" ^
  -d "{\"url\": \"https://example.com\"}"
```

### `POST /api/scrape/metadata`

```bash
curl -s -X POST "http://127.0.0.1:8000/api/scrape/metadata" ^
  -H "Content-Type: application/json" ^
  -d "{\"url\": \"https://example.com\"}"
```

### `GET /api/scrape/history`

Query: `skip` (default `0`), `limit` (default `20`, max `100`).

```bash
curl -s "http://127.0.0.1:8000/api/scrape/history?skip=0&limit=10"
```

## Project structure

```
11-Web-Scraper-API/
├── main.py              # FastAPI app, scraping logic, SQLite models
├── requirements.txt     # Python dependencies
├── README.md            # This file
└── scraper_history.db   # Created at runtime (SQLite)
```

## Notes

- Only `http` / `https` URLs are accepted.
- Non-HTML responses return `415`.
- Timeouts and connection failures map to `504` / `502` with a short detail message.
- Respect robots.txt and site terms; use responsibly.
