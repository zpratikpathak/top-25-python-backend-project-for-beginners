# Real-Time Notification Service

FastAPI service backed by SQLite for creating notifications, querying them with filters and pagination, and delivering new events in real time over **WebSocket** and **Server-Sent Events (SSE)**. Delivery honors per-user preferences (enabled types, channel, quiet hours).

## Features

- CRUD-style notification APIs with read state, bulk mark-read, stats, and deletion
- **Pub/sub hub** in process: one broadcaster fans out to all WebSocket and SSE subscribers for a `user_id`
- Preferences: enabled notification types, `websocket` / `sse` / `both`, optional quiet hours (`HH:MM`, UTC wall clock)
- Async SQLAlchemy with SQLite (`aiosqlite`)

## Architecture

```text
┌─────────────┐     POST /api/notifications      ┌──────────────┐
│   Client    │ ───────────────────────────────► │   FastAPI    │
└─────────────┘                                  └──────┬───────┘
       ▲                                                │
       │ WebSocket /ws/notifications/{user_id}          │ write DB
       │ SSE /api/notifications/stream/{user_id}        ▼
       │                                         ┌──────────────┐
       └──────── NotificationHub.publish ◄──────│  SQLite DB   │
                 (per-user WS + SSE queues)     └──────────────┘
```

When a notification is created, it is persisted, then the hub pushes a JSON payload to connected clients **only if** the user’s preferences allow that type, the current time is outside quiet hours, and the chosen delivery method includes WebSocket and/or SSE.

## Tech stack

- Python 3.11+
- FastAPI, Uvicorn
- SQLAlchemy 2 (async), SQLite via `aiosqlite`
- `sse-starlette` for SSE
- `websockets` (Uvicorn/FastAPI stack)

## Installation

```bash
cd 21-Real-Time-Notification-Service
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # Linux/macOS
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

Interactive docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

## API reference (curl)

**Create notification**

```bash
curl -s -X POST http://127.0.0.1:8000/api/notifications ^
  -H "Content-Type: application/json" ^
  -d "{\"user_id\":\"user-1\",\"title\":\"Hello\",\"message\":\"World\",\"type\":\"info\",\"priority\":\"normal\"}"
```

**List notifications** (optional: `read`, `type`, `skip`, `limit`)

```bash
curl -s "http://127.0.0.1:8000/api/notifications?user_id=user-1&read=false&type=info&skip=0&limit=10"
```

**Stats**

```bash
curl -s "http://127.0.0.1:8000/api/notifications/stats?user_id=user-1"
```

**Mark one as read**

```bash
curl -s -X PATCH http://127.0.0.1:8000/api/notifications/1/read
```

**Mark all as read**

```bash
curl -s -X PATCH "http://127.0.0.1:8000/api/notifications/read-all?user_id=user-1"
```

**Delete**

```bash
curl -s -X DELETE http://127.0.0.1:8000/api/notifications/1
```

**Get preferences** (creates defaults if missing)

```bash
curl -s http://127.0.0.1:8000/api/preferences/user-1
```

**Update preferences**

```bash
curl -s -X PUT http://127.0.0.1:8000/api/preferences/user-1 ^
  -H "Content-Type: application/json" ^
  -d "{\"enabled_types\":[\"info\",\"warning\"],\"delivery_method\":\"both\",\"quiet_hours_start\":\"22:00\",\"quiet_hours_end\":\"07:00\"}"
```

On Linux/macOS, replace line-ending carets `^` with `\` for curl continuation.

## WebSocket

Connect to:

`ws://127.0.0.1:8000/ws/notifications/{user_id}`

The server accepts the connection and pushes JSON objects for new notifications (same shape as the REST create response). Sending any text from the client keeps the connection alive.

Example (browser):

```javascript
const ws = new WebSocket("ws://127.0.0.1:8000/ws/notifications/user-1");
ws.onmessage = (ev) => console.log(JSON.parse(ev.data));
```

## SSE

Subscribe with GET:

`http://127.0.0.1:8000/api/notifications/stream/{user_id}`

- Event `notification`: `data` is a JSON string of the notification payload.
- Event `ping`: keep-alive every ~20s when idle.

Example (browser):

```javascript
const es = new EventSource("http://127.0.0.1:8000/api/notifications/stream/user-1");
es.addEventListener("notification", (e) => console.log(JSON.parse(e.data)));
```

## Project structure

```text
21-Real-Time-Notification-Service/
├── main.py              # App, models, hub, routes
├── requirements.txt
├── README.md
└── notifications.db     # created on first run (SQLite)
```

## Notes

- **Quiet hours** are evaluated using the server’s current UTC time and `HH:MM` strings; ranges that cross midnight are supported (e.g. `22:00`–`07:00`).
- If `enabled_types` is empty, real-time push is disabled for that user (notifications are still stored and available via REST).
