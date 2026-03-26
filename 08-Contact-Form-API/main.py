import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, g, jsonify, request

DATABASE = Path(__file__).resolve().parent / "contacts.db"
EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$")

app = Flask(__name__)


def get_db():
    if "db" not in g:
        conn = sqlite3.connect(DATABASE)
        conn.row_factory = sqlite3.Row
        g.db = conn
    return g.db


@app.teardown_appcontext
def close_db(_error):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    DATABASE.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DATABASE)
    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS contacts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL,
                email TEXT NOT NULL,
                subject TEXT NOT NULL,
                message TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'unread'
                    CHECK (status IN ('read', 'unread')),
                created_at TEXT NOT NULL
            )
            """
        )
        conn.commit()
    finally:
        conn.close()


def row_to_dict(row):
    return {
        "id": row["id"],
        "name": row["name"],
        "email": row["email"],
        "subject": row["subject"],
        "message": row["message"],
        "status": row["status"],
        "created_at": row["created_at"],
    }


@app.post("/api/contacts")
def create_contact():
    data = request.get_json(silent=True) or {}
    name = (data.get("name") or "").strip()
    email = (data.get("email") or "").strip()
    subject = (data.get("subject") or "").strip()
    message = (data.get("message") or "").strip()

    errors = []
    if not name:
        errors.append("name is required")
    if not email:
        errors.append("email is required")
    elif not EMAIL_PATTERN.match(email):
        errors.append("email format is invalid")
    if not subject:
        errors.append("subject is required")
    if not message:
        errors.append("message is required")

    if errors:
        return jsonify({"error": "validation failed", "details": errors}), 400

    created_at = datetime.now(timezone.utc).isoformat()
    db = get_db()
    cur = db.execute(
        """
        INSERT INTO contacts (name, email, subject, message, status, created_at)
        VALUES (?, ?, ?, ?, 'unread', ?)
        """,
        (name, email, subject, message, created_at),
    )
    db.commit()
    contact_id = cur.lastrowid
    row = db.execute(
        "SELECT * FROM contacts WHERE id = ?", (contact_id,)
    ).fetchone()
    return jsonify(row_to_dict(row)), 201


@app.get("/api/contacts/stats")
def contacts_stats():
    db = get_db()
    total = db.execute("SELECT COUNT(*) AS c FROM contacts").fetchone()["c"]
    read_count = db.execute(
        "SELECT COUNT(*) AS c FROM contacts WHERE status = 'read'"
    ).fetchone()["c"]
    unread = db.execute(
        "SELECT COUNT(*) AS c FROM contacts WHERE status = 'unread'"
    ).fetchone()["c"]
    return jsonify(
        {"total": total, "read": read_count, "unread": unread}
    )


@app.get("/api/contacts")
def list_contacts():
    status_filter = request.args.get("status", type=str)
    search = (request.args.get("search") or "").strip()
    page = request.args.get("page", default=1, type=int)
    per_page = request.args.get("per_page", default=20, type=int)

    if page < 1:
        return jsonify({"error": "page must be >= 1"}), 400
    if per_page < 1 or per_page > 100:
        return jsonify({"error": "per_page must be between 1 and 100"}), 400

    if status_filter is not None and status_filter not in ("read", "unread"):
        return jsonify({"error": "status must be read or unread"}), 400

    conditions = []
    params = []

    if status_filter:
        conditions.append("status = ?")
        params.append(status_filter)

    if search:
        like = f"%{search}%"
        conditions.append("(name LIKE ? OR subject LIKE ? OR message LIKE ?)")
        params.extend([like, like, like])

    where_sql = f"WHERE {' AND '.join(conditions)}" if conditions else ""

    db = get_db()
    count_row = db.execute(
        f"SELECT COUNT(*) AS c FROM contacts {where_sql}", params
    ).fetchone()
    total = count_row["c"]

    offset = (page - 1) * per_page
    list_params = list(params) + [per_page, offset]
    rows = db.execute(
        f"""
        SELECT * FROM contacts
        {where_sql}
        ORDER BY id DESC
        LIMIT ? OFFSET ?
        """,
        list_params,
    ).fetchall()

    return jsonify(
        {
            "items": [row_to_dict(r) for r in rows],
            "page": page,
            "per_page": per_page,
            "total": total,
            "total_pages": (total + per_page - 1) // per_page if total else 0,
        }
    )


@app.get("/api/contacts/<int:contact_id>")
def get_contact(contact_id):
    db = get_db()
    row = db.execute(
        "SELECT * FROM contacts WHERE id = ?", (contact_id,)
    ).fetchone()
    if row is None:
        return jsonify({"error": "not found"}), 404
    return jsonify(row_to_dict(row))


@app.patch("/api/contacts/<int:contact_id>/read")
def mark_read(contact_id):
    db = get_db()
    cur = db.execute(
        "UPDATE contacts SET status = 'read' WHERE id = ?", (contact_id,)
    )
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "not found"}), 404
    row = db.execute(
        "SELECT * FROM contacts WHERE id = ?", (contact_id,)
    ).fetchone()
    return jsonify(row_to_dict(row))


@app.patch("/api/contacts/<int:contact_id>/unread")
def mark_unread(contact_id):
    db = get_db()
    cur = db.execute(
        "UPDATE contacts SET status = 'unread' WHERE id = ?", (contact_id,)
    )
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "not found"}), 404
    row = db.execute(
        "SELECT * FROM contacts WHERE id = ?", (contact_id,)
    ).fetchone()
    return jsonify(row_to_dict(row))


@app.delete("/api/contacts/<int:contact_id>")
def delete_contact(contact_id):
    db = get_db()
    cur = db.execute("DELETE FROM contacts WHERE id = ?", (contact_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "not found"}), 404
    return "", 204


with app.app_context():
    init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
