import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, g, jsonify, request

DATABASE_PATH = Path(__file__).resolve().parent / "todos.db"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def get_db() -> sqlite3.Connection:
    db = getattr(g, "_database", None)
    if db is None:
        db = g._database = sqlite3.connect(DATABASE_PATH)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys = ON")
    return db


def init_db() -> None:
    DATABASE_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE_PATH)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS todos (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                title TEXT NOT NULL,
                description TEXT NOT NULL DEFAULT '',
                completed INTEGER NOT NULL DEFAULT 0 CHECK (completed IN (0, 1)),
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def row_to_todo(row: sqlite3.Row) -> dict:
    return {
        "id": row["id"],
        "title": row["title"],
        "description": row["description"],
        "completed": bool(row["completed"]),
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


app = Flask(__name__)
init_db()


@app.teardown_appcontext
def close_db(_exc) -> None:
    db = getattr(g, "_database", None)
    if db is not None:
        db.close()


@app.get("/api/todos")
def list_todos():
    completed_param = request.args.get("completed", type=str)
    db = get_db()
    if completed_param is None:
        cur = db.execute(
            "SELECT id, title, description, completed, created_at, updated_at "
            "FROM todos ORDER BY id ASC"
        )
    else:
        val = completed_param.lower()
        if val not in ("true", "false"):
            return (
                jsonify(
                    {
                        "error": "Invalid query parameter 'completed'; use true or false."
                    }
                ),
                400,
            )
        flag = 1 if val == "true" else 0
        cur = db.execute(
            "SELECT id, title, description, completed, created_at, updated_at "
            "FROM todos WHERE completed = ? ORDER BY id ASC",
            (flag,),
        )
    rows = cur.fetchall()
    return jsonify([row_to_todo(r) for r in rows])


@app.get("/api/todos/<int:todo_id>")
def get_todo(todo_id: int):
    db = get_db()
    cur = db.execute(
        "SELECT id, title, description, completed, created_at, updated_at "
        "FROM todos WHERE id = ?",
        (todo_id,),
    )
    row = cur.fetchone()
    if row is None:
        return jsonify({"error": "Todo not found."}), 404
    return jsonify(row_to_todo(row))


@app.post("/api/todos")
def create_todo():
    if not request.is_json:
        return jsonify({"error": "Request body must be JSON."}), 415
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Invalid JSON body."}), 400
    if not isinstance(data, dict):
        return jsonify({"error": "JSON body must be an object."}), 400
    title = data.get("title")
    if title is None or (isinstance(title, str) and not title.strip()):
        return jsonify({"error": "Field 'title' is required and must be non-empty."}), 400
    if not isinstance(title, str):
        return jsonify({"error": "Field 'title' must be a string."}), 400
    description = data.get("description", "")
    if description is not None and not isinstance(description, str):
        return jsonify({"error": "Field 'description' must be a string."}), 400
    if description is None:
        description = ""
    now = utc_now_iso()
    db = get_db()
    cur = db.execute(
        "INSERT INTO todos (title, description, completed, created_at, updated_at) "
        "VALUES (?, ?, 0, ?, ?)",
        (title.strip(), description, now, now),
    )
    db.commit()
    new_id = cur.lastrowid
    cur = db.execute(
        "SELECT id, title, description, completed, created_at, updated_at "
        "FROM todos WHERE id = ?",
        (new_id,),
    )
    row = cur.fetchone()
    return jsonify(row_to_todo(row)), 201


@app.put("/api/todos/<int:todo_id>")
def update_todo(todo_id: int):
    if not request.is_json:
        return jsonify({"error": "Request body must be JSON."}), 415
    data = request.get_json(silent=True)
    if data is None:
        return jsonify({"error": "Invalid JSON body."}), 400
    if not isinstance(data, dict):
        return jsonify({"error": "JSON body must be an object."}), 400
    db = get_db()
    cur = db.execute(
        "SELECT id, title, description, completed, created_at, updated_at "
        "FROM todos WHERE id = ?",
        (todo_id,),
    )
    row = cur.fetchone()
    if row is None:
        return jsonify({"error": "Todo not found."}), 404
    title = data.get("title", row["title"])
    description = data.get("description", row["description"])
    completed = data.get("completed", bool(row["completed"]))
    if not isinstance(title, str) or not title.strip():
        return jsonify({"error": "Field 'title' must be a non-empty string."}), 400
    if not isinstance(description, str):
        return jsonify({"error": "Field 'description' must be a string."}), 400
    if not isinstance(completed, bool):
        return jsonify({"error": "Field 'completed' must be a boolean."}), 400
    now = utc_now_iso()
    db.execute(
        "UPDATE todos SET title = ?, description = ?, completed = ?, updated_at = ? "
        "WHERE id = ?",
        (title.strip(), description, 1 if completed else 0, now, todo_id),
    )
    db.commit()
    cur = db.execute(
        "SELECT id, title, description, completed, created_at, updated_at "
        "FROM todos WHERE id = ?",
        (todo_id,),
    )
    return jsonify(row_to_todo(cur.fetchone()))


@app.delete("/api/todos/<int:todo_id>")
def delete_todo(todo_id: int):
    db = get_db()
    cur = db.execute("DELETE FROM todos WHERE id = ?", (todo_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "Todo not found."}), 404
    return "", 204


@app.patch("/api/todos/<int:todo_id>/toggle")
def toggle_todo(todo_id: int):
    db = get_db()
    cur = db.execute(
        "SELECT id, title, description, completed, created_at, updated_at "
        "FROM todos WHERE id = ?",
        (todo_id,),
    )
    row = cur.fetchone()
    if row is None:
        return jsonify({"error": "Todo not found."}), 404
    new_completed = 0 if row["completed"] else 1
    now = utc_now_iso()
    db.execute(
        "UPDATE todos SET completed = ?, updated_at = ? WHERE id = ?",
        (new_completed, now, todo_id),
    )
    db.commit()
    cur = db.execute(
        "SELECT id, title, description, completed, created_at, updated_at "
        "FROM todos WHERE id = ?",
        (todo_id,),
    )
    return jsonify(row_to_todo(cur.fetchone()))


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=False)
