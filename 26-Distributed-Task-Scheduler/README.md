# Distributed Task Scheduler

FastAPI service that stores scheduled tasks in SQLite, runs a background polling loop, supports task dependencies (DAG), execution history, SQLite-backed per-task locks, and retries with exponential backoff.

## Features

- **CRUD tasks** with cron or one-time `run_at`, JSON payload, enable/disable, and manual trigger
- **Dependencies** between tasks with cycle detection when adding edges
- **DAG runs** via API: topological order, sequential execution, fail-fast on first failure
- **Scheduler** toggled with start/stop; tick every 10 seconds for due tasks
- **Distributed lock** (`task_locks`): one row per task; expired locks are reclaimed; workers identify with `hostname:pid:uuid`
- **Retries**: after a failed run, `next_run_at` is delayed by exponential backoff until `max_retries` is exhausted, then the next cron slot is used again for recurring tasks

## Cron expression format

Five fields separated by spaces, in order:

`minute hour day-of-month month day-of-week`

| Field | Range | Notes |
|-------|--------|--------|
| minute | 0–59 | |
| hour | 0–23 | |
| day-of-month | 1–31 | |
| month | 1–12 | |
| day-of-week | 0–7 | `0` and `7` are Sunday; `1` is Monday |

Supported patterns per field:

- `*` — any value
- `n` — single value
- `n-m` — inclusive range
- `*/step` — step through the full range (e.g. `*/5` every five minutes)
- `n-m/step` — step within a range
- Comma-separated lists (e.g. `1,15,30`)

If both day-of-month and day-of-week are `*`, any day matches. If one is `*` and the other is restricted, only the restricted field is used. If both are restricted, a time matches if **either** the DOM or DOW condition holds (typical cron OR semantics).

## DAG execution

- `POST /api/dag/execute` accepts `{"task_ids": [...]}`.
- Only dependencies **between tasks in that list** are considered; execution order is a topological sort of that subgraph.
- Tasks run **one after another** in that order. If any task fails, the DAG is marked `failed`, remaining tasks are **not** run (fail-fast).
- Each step creates an `Execution` row linked to the same `DagExecution`.

Scheduled runs **respect dependencies**: a task is not executed until every dependency’s **latest** execution has `status` `success`.

## Built-in task types

| `task_type` | Behavior |
|-------------|----------|
| `noop` | Returns `{"ok": true}` |
| `echo` | Returns `{"echo": <payload>}` |
| `fail` | Raises an error; optional `payload.message` |

Add more handlers in `run_task_handler` in `main.py` as needed.

## Tech stack

- Python 3.10+
- FastAPI
- Uvicorn
- SQLAlchemy 2.x
- SQLite (file `scheduler.db` in the project directory by default)

## Installation

```bash
cd 26-Distributed-Task-Scheduler
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate   # Linux/macOS
pip install -r requirements.txt
```

## Run (port 8000)

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Or:

```bash
python main.py
```

Optional: `set DATABASE_URL=sqlite:///./custom.db` (Windows) to change the database file.

After startup, enable the scheduler:

```bash
curl -X POST http://127.0.0.1:8000/api/scheduler/start
```

## API examples (curl)

**Create a cron task**

```bash
curl -s -X POST http://127.0.0.1:8000/api/tasks ^
  -H "Content-Type: application/json" ^
  -d "{\"name\":\"hourly\",\"task_type\":\"echo\",\"cron_expression\":\"0 * * * *\",\"payload\":{\"k\":\"v\"},\"enabled\":true,\"max_retries\":3}"
```

**Create a one-time task**

```bash
curl -s -X POST http://127.0.0.1:8000/api/tasks ^
  -H "Content-Type: application/json" ^
  -d "{\"name\":\"once\",\"task_type\":\"noop\",\"run_at\":\"2026-12-31T23:00:00Z\",\"payload\":{},\"enabled\":true,\"max_retries\":1}"
```

**List tasks (filters + pagination)**

```bash
curl -s "http://127.0.0.1:8000/api/tasks?enabled=true&task_type=echo&skip=0&limit=10"
```

**Get task with history**

```bash
curl -s "http://127.0.0.1:8000/api/tasks/1?history_limit=20"
```

**Update / delete**

```bash
curl -s -X PUT http://127.0.0.1:8000/api/tasks/1 -H "Content-Type: application/json" -d "{\"enabled\":false}"
curl -s -X DELETE http://127.0.0.1:8000/api/tasks/1
```

**Trigger, enable, disable**

```bash
curl -s -X POST http://127.0.0.1:8000/api/tasks/1/trigger
curl -s -X PATCH http://127.0.0.1:8000/api/tasks/1/enable
curl -s -X PATCH http://127.0.0.1:8000/api/tasks/1/disable
```

**Dependencies**

```bash
curl -s -X POST http://127.0.0.1:8000/api/tasks/2/dependencies -H "Content-Type: application/json" -d "{\"depends_on_task_id\":1}"
curl -s http://127.0.0.1:8000/api/tasks/2/dependencies
curl -s -X DELETE http://127.0.0.1:8000/api/tasks/2/dependencies/1
```

**DAG**

```bash
curl -s -X POST http://127.0.0.1:8000/api/dag/execute -H "Content-Type: application/json" -d "{\"task_ids\":[1,2,3]}"
curl -s http://127.0.0.1:8000/api/dag/1/status
```

**Executions**

```bash
curl -s "http://127.0.0.1:8000/api/executions?task_id=1&status=success&skip=0&limit=20"
curl -s http://127.0.0.1:8000/api/executions/1
```

**Scheduler**

```bash
curl -s http://127.0.0.1:8000/api/scheduler/status
curl -s -X POST http://127.0.0.1:8000/api/scheduler/start
curl -s -X POST http://127.0.0.1:8000/api/scheduler/stop
```

On Linux/macOS, replace `^` line continuations with `\` and use single-line JSON if preferred.

## Project structure

```
26-Distributed-Task-Scheduler/
├── main.py              # App, models, cron parser, scheduler, API
├── requirements.txt
├── README.md
└── scheduler.db         # Created on first run (SQLite)
```

## Endpoint summary

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/tasks` | Create task |
| GET | `/api/tasks` | List tasks (`enabled`, `task_type`, `skip`, `limit`) |
| GET | `/api/tasks/{id}` | Task + execution history |
| PUT | `/api/tasks/{id}` | Update task |
| DELETE | `/api/tasks/{id}` | Delete task |
| POST | `/api/tasks/{id}/trigger` | Run now |
| PATCH | `/api/tasks/{id}/enable` | Enable |
| PATCH | `/api/tasks/{id}/disable` | Disable |
| POST | `/api/tasks/{id}/dependencies` | Add dependency |
| GET | `/api/tasks/{id}/dependencies` | List dependencies |
| DELETE | `/api/tasks/{id}/dependencies/{dep_id}` | Remove dependency |
| POST | `/api/dag/execute` | Run DAG subset |
| GET | `/api/dag/{id}/status` | DAG status |
| GET | `/api/executions` | List executions |
| GET | `/api/executions/{id}` | Execution detail |
| GET | `/api/scheduler/status` | Scheduler + upcoming tasks |
| POST | `/api/scheduler/start` | Start background scheduler |
| POST | `/api/scheduler/stop` | Stop scheduler |

OpenAPI docs: `http://127.0.0.1:8000/docs`.
