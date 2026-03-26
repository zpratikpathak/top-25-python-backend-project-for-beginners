# Real-Time Chat App

A small backend for multi-room chat: REST APIs for rooms and message history, plus WebSockets for live messaging. Messages (chat, join, leave) are stored in SQLite for pagination and replay.

## Features

- **WebSocket rooms** — Clients connect with a room name and username; everyone in the room receives broadcasts.
- **Join / leave** — System messages are stored and broadcast when users connect or disconnect.
- **SQLite persistence** — Chat history with pagination; reconnect and fetch past messages via HTTP.
- **Live presence** — List rooms with approximate active user counts and query who is connected in a room.

## Tech stack

- Python 3.11+
- [FastAPI](https://fastapi.tiangolo.com/) — HTTP + WebSocket
- [SQLAlchemy 2.0](https://www.sqlalchemy.org/) — ORM
- [SQLite](https://www.sqlite.org/) — file database (`chat.db`)
- [Uvicorn](https://www.uvicorn.org/) — ASGI server

## Installation

```bash
cd 14-Real-Time-Chat-App
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run the server

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

Or:

```bash
python main.py
```

Interactive docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

## API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/rooms` | Create a room. JSON body: `name` (string), `description` (string, optional). Returns `409` if the name exists. |
| `GET` | `/api/rooms` | List rooms with `active_users` (WebSocket count per room). |
| `GET` | `/api/rooms/{room_name}/history` | Message history. Query: `skip` (default `0`), `limit` (default `50`, max `200`). Newest-first in DB; response `messages` are oldest-first in the page. |
| `GET` | `/api/rooms/{room_name}/users` | Usernames currently connected via WebSocket in that room. |

## WebSocket

**URL:** `ws://127.0.0.1:8000/ws/{room_name}?username={name}`

- The room must exist (create it with `POST /api/rooms` first).
- `username` is required on the query string.
- If the same `username` connects again in the same room, the previous connection is closed.

**Inbound:** plain text — each frame is stored as a `chat` message and broadcast as JSON.

**Outbound:** JSON objects:

```json
{
  "type": "chat",
  "username": "alice",
  "content": "Hello",
  "timestamp": "2025-03-26T12:00:00+00:00"
}
```

`type` is one of `chat`, `join`, or `leave`.

## Testing

### HTTP (curl)

Create a room:

```bash
curl -X POST http://127.0.0.1:8000/api/rooms -H "Content-Type: application/json" -d "{\"name\":\"general\",\"description\":\"Main room\"}"
```

List rooms and history:

```bash
curl http://127.0.0.1:8000/api/rooms
curl "http://127.0.0.1:8000/api/rooms/general/history?skip=0&limit=10"
curl http://127.0.0.1:8000/api/rooms/general/users
```

### websocat

Install [websocat](https://github.com/vi/websocat), then:

```bash
websocat "ws://127.0.0.1:8000/ws/general?username=alice"
```

Type a line and press Enter to send; JSON events appear for join, others’ messages, leave, etc.

### Browser (JavaScript)

Open any page on `http://127.0.0.1:8000` (or a static file) and run:

```javascript
const ws = new WebSocket("ws://127.0.0.1:8000/ws/general?username=browser1");
ws.onmessage = (e) => console.log(JSON.parse(e.data));
ws.onopen = () => ws.send("Hello from the browser");
```

Note: for cross-origin pages, the server enables permissive CORS for the REST API; WebSockets use a separate handshake and are not subject to the same CORS rules as `fetch`.

## Project structure

```
14-Real-Time-Chat-App/
├── main.py           # FastAPI app, models, ConnectionManager, routes
├── requirements.txt  # Python dependencies
├── README.md         # This file
└── chat.db           # Created at runtime (SQLite)
```
