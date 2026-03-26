# Job Queue System

A small **FastAPI** service with **SQLite** persistence and **background worker threads** that claim jobs, simulate work, apply **exponential backoff** retries, and route exhausted jobs to a **dead-letter** state. Jobs are ordered by **priority** (10 = highest) then **FIFO** by `created_at`.

## Features

- Submit jobs with JSON payload, priority (1–10), and configurable `max_retries` (default 3)
- List and filter jobs by status with pagination
- Cancel pending jobs, retry failed jobs, requeue dead-letter jobs
- Queue statistics: counts per status and average completed-job duration (milliseconds)
- Multiple concurrent workers (default 3), started on application lifespan
- REST API documented automatically at `/docs`

## Architecture (text diagram)

```
┌─────────────┐     POST/GET/...      ┌──────────────────┐
│   Client    │ ────────────────────► │    FastAPI       │
└─────────────┘                       │  (HTTP + CRUD)   │
                                      └────────┬─────────┘
                                               │
                                               ▼
                                      ┌──────────────────┐
                                      │  SQLite (jobs)   │
                                      │  SQLAlchemy ORM  │
                                      └────────▲─────────┘
                                               │
                    poll: pending/failed       │
                    priority DESC, created_at  │
                    scheduled_at <= now        │
                                      ┌────────┴─────────┐
                                      │ Worker threads   │
                                      │ (claim → run →   │
                                      │  complete /      │
                                      │  retry / D/L)    │
                                      └──────────────────┘
```

**Lifecycle summary:** `pending` → `running` → `completed`, or on failure → `failed` with `scheduled_at` (backoff) until retries are exhausted → `dead_letter`. Manual `cancelled` only for pending jobs.

## Tech stack

- Python 3.10+
- FastAPI, Uvicorn
- SQLAlchemy 2.x, SQLite (`jobs.db` in the project directory by default)

## Installation

```bash
cd 19-Job-Queue-System
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

### Environment variables

| Variable | Default | Description |
|----------|---------|-------------|
| `DATABASE_URL` | `sqlite:///./jobs.db` | SQLAlchemy database URL |
| `WORKER_COUNT` | `3` | Number of worker threads |
| `WORKER_POLL_INTERVAL_SEC` | `0.25` | Sleep when no job is available |

## API endpoints

Base URL: `http://localhost:8000`

### Submit a job

```bash
curl -s -X POST http://localhost:8000/api/jobs ^
  -H "Content-Type: application/json" ^
  -d "{\"job_type\": \"email.send\", \"payload\": {\"to\": \"a@b.com\"}, \"priority\": 10, \"max_retries\": 3}"
```

### List jobs (filter + pagination)

```bash
curl -s "http://localhost:8000/api/jobs?status=pending&skip=0&limit=20"
```

### Job details

```bash
curl -s http://localhost:8000/api/jobs/<JOB_ID>
```

### Cancel a pending job

```bash
curl -s -X DELETE http://localhost:8000/api/jobs/<JOB_ID>
```

### Retry a failed job

```bash
curl -s -X POST http://localhost:8000/api/jobs/<JOB_ID>/retry
```

### Queue statistics

```bash
curl -s http://localhost:8000/api/jobs/stats
```

### Dead-letter list

```bash
curl -s "http://localhost:8000/api/dead-letter?skip=0&limit=50"
```

### Requeue from dead-letter

```bash
curl -s -X POST http://localhost:8000/api/dead-letter/<JOB_ID>/requeue
```

On Unix shells, replace `^` line continuations with `\` and use single quotes around JSON where convenient.

## Job lifecycle

1. **pending** — Eligible when `scheduled_at` is null or in the past. Workers pick the next row by `priority DESC`, `created_at ASC`.
2. **running** — A worker claimed the row and sets `started_at`.
3. **completed** — Success; optional `result` JSON and `completed_at`.
4. **failed** — Processing error with retries remaining; `attempts` increased, `scheduled_at` set using exponential backoff (base 2 seconds, capped). Becomes eligible again when `scheduled_at` passes (still queried as retryable with `pending`).
5. **dead_letter** — After a failed run, `attempts` is incremented; when `attempts >= max_retries` the job moves here (with default `max_retries=3`, that is after three failed runs). Use `POST /api/dead-letter/{id}/requeue` to reset and return to **pending**.
6. **cancelled** — Only from **pending** via `DELETE /api/jobs/{id}`.

Manual **retry** (`POST /api/jobs/{id}/retry`) clears backoff and error for **failed** jobs and sets **pending** immediately.

Workers simulate work with a short sleep and random success/failure (for demonstration).

## Project structure

```
19-Job-Queue-System/
├── main.py           # App, model, workers, routes
├── requirements.txt
├── README.md
└── jobs.db           # Created on first run (SQLite file)
```

Interactive docs: [http://localhost:8000/docs](http://localhost:8000/docs)
