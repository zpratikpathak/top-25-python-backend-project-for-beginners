# Real-Time Collaborative Editor (FastAPI + WebSocket + SQLite)

Backend for a multi-user text editor: REST CRUD for documents, versioned operation log, Operational Transform (OT) on the server, WebSocket sync, and cursor presence.

## Features

- **Documents**: Create, list (paginated), read, rename (title), delete.
- **Version history**: Every insert/delete is stored; any past version can be reconstructed by replaying operations from an empty string.
- **Real-time editing**: WebSocket per document; clients send operations with a `base_version`; the server transforms against concurrent ops, applies, increments version, persists, and broadcasts.
- **Presence**: Active collaborators and optional cursor positions via WebSocket and `GET /api/documents/{id}/collaborators`.

## Tech stack

- Python 3.11+
- [FastAPI](https://fastapi.tiangolo.com/)
- [Uvicorn](https://www.uvicorn.org/)
- [SQLAlchemy 2.0](https://www.sqlalchemy.org/) + SQLite
- [websockets](https://websockets.readthedocs.io/) (Uvicorn dependency for WS)

## Operational transform (simplified)

The server is **authoritative** for document state and version numbers.

1. Each document has a monotonic integer `version` (0 = no operations yet, or only bootstrap state as described below).
2. The client includes **`base_version`**: the document version the client had when it produced the operation (in that client’s coordinate space).
3. The server loads all persisted operations with `version > base_version` in order. Each of those ops was already applied to the canonical document; the client’s pending op is **transformed** against each one so it applies correctly on the current string.
4. The transformed op is applied to `Document.content`, stored as a new `Operation` row with `version = previous + 1`, and broadcast to other sockets as `op_applied`.

### Transform pairs

Coordinates refer to the document **before** the server op is applied when transforming the client op “against” an already-applied server op:

| Client \\ Server | Insert | Delete |
|------------------|--------|--------|
| **Insert** | If client index ≥ server index, shift client index by `len(server.text)` (tie: after server insert). | If client index lies inside deleted range, clamp to delete start; if after range, subtract delete length. |
| **Delete** | If server insert is before delete start, shift start; if inside range, extend delete length. | Classic interval adjustment: non-overlapping shifts; full containment → no-op; partial overlaps → shortened or shifted ranges. |

This is a **pragmatic subset** of OT suitable for a linear text buffer; it is not a full CRDT.

### Initial content and version 0

- **Empty create**: `version` stays `0`, no rows in `operations`. Version `0` content is `""`.
- **Create with `content`**: A bootstrap **insert** at position `0` is stored as `version == 1` with user `__system__`. Replay from empty yields the same string as the current document for each version.

## Operation format (HTTP history / DB)

Stored fields map to:

| Field | Meaning |
|-------|---------|
| `op_type` | `"insert"` or `"delete"` |
| `position` | Character index (UTF-16/code-unit style is not modeled; treat as Python string indices) |
| `text` | Insert payload (insert only) |
| `delete_length` | Delete length (delete only) |
| `version` | Version after this operation is applied |
| `user` | Username (or `__system__` for bootstrap) |

## Installation

```bash
cd 27-Real-Time-Collaborative-Editor
python -m venv .venv
.venv\Scripts\activate   # Windows
# source .venv/bin/activate  # Linux/macOS
pip install -r requirements.txt
```

## Run (port 8000)

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Interactive API docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

## REST API

| Method | Path | Description |
|--------|------|-------------|
| `POST` | `/api/documents` | Body: `{ "title": str, "content": str? }` — creates document |
| `GET` | `/api/documents` | Query: `skip`, `limit` — paginated list |
| `GET` | `/api/documents/{id}` | Full document including current `content` and `version` |
| `PUT` | `/api/documents/{id}` | Body: `{ "title": str }` |
| `DELETE` | `/api/documents/{id}` | Deletes document and cascades operations |
| `GET` | `/api/documents/{id}/history` | Ordered operation log |
| `GET` | `/api/documents/{id}/version/{version}` | Reconstructed `content` at `version` |
| `GET` | `/api/documents/{id}/collaborators` | In-memory active sessions + cursors |

## WebSocket

**URL:** `ws://127.0.0.1:8000/ws/documents/{document_id}?username={name}`

### Server → client

- `sync` — `{ "type": "sync", "content", "version", "title" }` right after connect.
- `presence` — `{ "type": "presence", "collaborators": [{ "username", "cursor_position" }] }`.
- `user_joined` / `user_left` — include updated `collaborators` list.
- `cursor` — another user moved: `{ "type": "cursor", "username", "position" }`.
- `op_applied` — canonical op applied on server: `{ "type", "op_type", "position", "version", "user", "text"? | "length"? }`.
- `ack` — `{ "type": "ack", "version", "op"?, "noop"? }` for your op (or noop).
- `resync` — client `base_version` ahead of server: full `{ "content", "version" }`.
- `error` — `{ "detail": "..." }`.

### Client → server

**Insert**

```json
{ "type": "insert", "position": 0, "text": "hi", "base_version": 0 }
```

**Delete**

```json
{ "type": "delete", "position": 0, "length": 1, "base_version": 1 }
```

**Cursor** (no `base_version`)

```json
{ "type": "cursor", "position": 5 }
```

Clients should apply remote `op_applied` messages to their local buffer and keep `base_version` in sync with the server `version` after each local ack.

## Testing concurrent editing

1. Start Uvicorn on port 8000.
2. `POST /api/documents` with `{ "title": "Demo", "content": "" }` and note `id`.
3. Open two WebSocket clients (e.g. two browser tabs with a small WS test page, or `wscat`), both with different `username` query params.
4. From both, send inserts with the **same** `base_version` (e.g. `0`) at the **same** `position` — both should succeed; server transforms the second; both clients should converge after applying broadcast ops.
5. `GET /api/documents/{id}` and `GET /api/documents/{id}/history` to verify content and log.

Example with Python (requires `websockets` installed):

```python
import asyncio, json
import websockets

async def client(name, uri):
    async with websockets.connect(uri) as ws:
        print(name, await ws.recv())
        print(name, await ws.recv())
        await ws.send(json.dumps({"type": "insert", "position": 0, "text": name, "base_version": 0}))
        print(name, await ws.recv())

async def main():
    doc_id = 1
    base = f"ws://127.0.0.1:8000/ws/documents/{doc_id}?username="
    await asyncio.gather(
        client("a", base + "alice"),
        client("b", base + "bob"),
    )

asyncio.run(main())
```

## Project structure

```
27-Real-Time-Collaborative-Editor/
├── main.py              # App, models, OT, REST, WebSocket
├── requirements.txt
├── README.md
└── collab_editor.db     # Created at runtime (SQLite)
```

## License

Use freely for learning and demos.
