# Poll Voting System

REST API for creating polls, casting votes, and watching results in real time over Server-Sent Events (SSE). Duplicate votes on the same poll are blocked using a stable voter identifier from the `X-Voter-Id` header, the `voter_id` cookie, or the client IP (with `X-Forwarded-For` when present).

## Features

- Create polls with a question, multiple options, and optional `expires_at` timestamp
- List polls with pagination (`skip`, `limit`) and `active` filter (`true` / `false`)
- Fetch a poll with per-option vote counts and total votes
- Vote once per voter per poll (unique constraint on `poll_id` + `voter_identifier`)
- Delete a poll and all related options and votes
- SSE stream of result updates after each vote (`snapshot` then `update` events)
- SQLite persistence via SQLAlchemy async engine (`aiosqlite`)

## Tech stack

- Python 3.11+
- FastAPI
- Uvicorn
- SQLAlchemy 2.x (async) + SQLite
- sse-starlette (`EventSourceResponse`, `JSONServerSentEvent`)

## Installation

```bash
cd 13-Poll-Voting-System
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Or:

```bash
python main.py
```

The database file `polls.db` is created in the project directory on first startup.

## API reference

### Create poll

```bash
curl -s -X POST http://127.0.0.1:8000/api/polls ^
  -H "Content-Type: application/json" ^
  -d "{\"question\": \"Favorite language?\", \"options\": [\"Python\", \"Rust\", \"Go\"]}"
```

With optional expiry (ISO 8601):

```bash
curl -s -X POST http://127.0.0.1:8000/api/polls ^
  -H "Content-Type: application/json" ^
  -d "{\"question\": \"Quick poll\", \"options\": [\"Yes\", \"No\"], \"expires_at\": \"2026-12-31T23:59:59Z\"}"
```

### List polls (pagination + active filter)

```bash
curl -s "http://127.0.0.1:8000/api/polls?skip=0&limit=10&active=true"
```

Omit `active` to return all polls. `active=true` returns polls that are both `is_active` and not past `expires_at`.

### Get poll with results

```bash
curl -s http://127.0.0.1:8000/api/polls/1
```

### Vote

Using default identification (IP / cookie set on first vote):

```bash
curl -s -X POST http://127.0.0.1:8000/api/polls/1/vote ^
  -H "Content-Type: application/json" ^
  -c cookies.txt -b cookies.txt ^
  -d "{\"option_id\": 1}"
```

Using a custom voter id (e.g. logged-in user id):

```bash
curl -s -X POST http://127.0.0.1:8000/api/polls/1/vote ^
  -H "Content-Type: application/json" ^
  -H "X-Voter-Id: user-42" ^
  -d "{\"option_id\": 2}"
```

### Delete poll

```bash
curl -s -X DELETE http://127.0.0.1:8000/api/polls/1
```

### SSE — live results

`curl` prints events until you stop it; use `--max-time` for a short sample:

**Windows (PowerShell)** — use `curl.exe` for real curl:

```powershell
curl.exe -N --max-time 15 "http://127.0.0.1:8000/api/polls/1/results/stream"
```

**Linux / macOS:**

```bash
curl -N --max-time 15 "http://127.0.0.1:8000/api/polls/1/results/stream"
```

- First event: `event: snapshot` with JSON `poll_id`, `question`, `options` (with `vote_count`), `total_votes`
- Later events: `event: update` with the same shape when someone votes
- Periodic comments are sent as keepalives by the SSE library (`ping` interval)

Open another terminal and POST a vote to the same poll while the stream runs to see `update` events.

## Project structure

```
13-Poll-Voting-System/
├── main.py            # FastAPI app, models, broadcaster, routes
├── requirements.txt   # Python dependencies
├── README.md          # This file
└── polls.db           # SQLite database (created at runtime)
```

## Notes

- Voter resolution order: `X-Voter-Id` header → `voter_id` cookie → `X-Forwarded-For` (first hop) → `request.client.host`. If no header or cookie was used, the response may set `voter_id` to the same value as the stored identifier so the browser can send it on later requests.
- SSE subscribers are kept in memory (`asyncio.Queue` per connection); restarting the server clears subscriber lists (clients reconnect).
- Interactive docs: `http://127.0.0.1:8000/docs`
