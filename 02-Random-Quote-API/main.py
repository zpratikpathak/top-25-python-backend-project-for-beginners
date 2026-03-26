import os
import random
import sqlite3
from datetime import datetime, timezone

from flask import Flask, g, jsonify, request

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "quotes.db")

SEED_QUOTES = [
    ("The only way to do great work is to love what you do.", "Steve Jobs", "success"),
    ("Success is not final, failure is not fatal: it is the courage to continue that counts.", "Winston Churchill", "success"),
    ("I have not failed. I've just found 10,000 ways that won't work.", "Thomas Edison", "success"),
    ("Believe you can and you're halfway there.", "Theodore Roosevelt", "inspirational"),
    ("What lies behind us and what lies before us are tiny matters compared to what lies within us.", "Ralph Waldo Emerson", "inspirational"),
    ("The future belongs to those who believe in the beauty of their dreams.", "Eleanor Roosevelt", "inspirational"),
    ("Life is what happens when you're busy making other plans.", "John Lennon", "life"),
    ("In three words I can sum up everything I've learned about life: it goes on.", "Robert Frost", "life"),
    ("The purpose of our lives is to be happy.", "Dalai Lama", "life"),
    ("The unexamined life is not worth living.", "Socrates", "wisdom"),
    ("Knowing yourself is the beginning of all wisdom.", "Aristotle", "wisdom"),
    ("The only true wisdom is in knowing you know nothing.", "Socrates", "wisdom"),
    ("I think, therefore I am.", "René Descartes", "wisdom"),
    ("I have never let my schooling interfere with my education.", "Mark Twain", "humor"),
    ("I am so clever that sometimes I don't understand a single word of what I am saying.", "Oscar Wilde", "humor"),
    ("Always forgive your enemies; nothing annoys them so much.", "Oscar Wilde", "humor"),
    ("I can resist everything except temptation.", "Oscar Wilde", "humor"),
    ("It does not matter how slowly you go as long as you do not stop.", "Confucius", "success"),
    ("Happiness is not something ready made. It comes from your own actions.", "Dalai Lama", "inspirational"),
    ("Do not dwell in the past, do not dream of the future, concentrate the mind on the present moment.", "Buddha", "wisdom"),
    ("The journey of a thousand miles begins with one step.", "Lao Tzu", "life"),
    ("Be yourself; everyone else is already taken.", "Oscar Wilde", "humor"),
]

app = Flask(__name__)


def get_db():
    if "db" not in g:
        g.db = sqlite3.connect(DB_PATH)
        g.db.row_factory = sqlite3.Row
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    db = g.pop("db", None)
    if db is not None:
        db.close()


def init_db():
    db = sqlite3.connect(DB_PATH)
    try:
        db.execute(
            """
            CREATE TABLE IF NOT EXISTS quotes (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                text TEXT NOT NULL,
                author TEXT NOT NULL,
                category TEXT NOT NULL,
                created_at TEXT NOT NULL DEFAULT (datetime('now'))
            )
            """
        )
        db.commit()
        count = db.execute("SELECT COUNT(*) FROM quotes").fetchone()[0]
        if count == 0:
            now = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M:%S")
            db.executemany(
                "INSERT INTO quotes (text, author, category, created_at) VALUES (?, ?, ?, ?)",
                [(t, a, c, now) for t, a, c in SEED_QUOTES],
            )
            db.commit()
    finally:
        db.close()


def row_to_dict(row):
    return {
        "id": row["id"],
        "text": row["text"],
        "author": row["author"],
        "category": row["category"],
        "created_at": row["created_at"],
    }


@app.get("/api/quotes/random")
def random_quote():
    category = request.args.get("category", type=str)
    db = get_db()
    if category:
        rows = db.execute(
            "SELECT * FROM quotes WHERE LOWER(category) = LOWER(?)",
            (category.strip(),),
        ).fetchall()
    else:
        rows = db.execute("SELECT * FROM quotes").fetchall()
    if not rows:
        return jsonify({"error": "No quotes found"}), 404
    return jsonify(row_to_dict(random.choice(rows)))


@app.get("/api/quotes")
def list_quotes():
    author = request.args.get("author")
    category = request.args.get("category")
    page = request.args.get("page", default=1, type=int)
    per_page = request.args.get("per_page", default=20, type=int)
    if page < 1:
        page = 1
    per_page = max(1, min(per_page, 100))

    conditions = []
    params = []
    if author:
        conditions.append("author LIKE ?")
        params.append(f"%{author.strip()}%")
    if category:
        conditions.append("LOWER(category) = LOWER(?)")
        params.append(category.strip())

    where = f"WHERE {' AND '.join(conditions)}" if conditions else ""
    db = get_db()

    total = db.execute(f"SELECT COUNT(*) FROM quotes {where}", params).fetchone()[0]
    offset = (page - 1) * per_page
    rows = db.execute(
        f"SELECT * FROM quotes {where} ORDER BY id ASC LIMIT ? OFFSET ?",
        [*params, per_page, offset],
    ).fetchall()

    return jsonify(
        {
            "quotes": [row_to_dict(r) for r in rows],
            "total": total,
            "page": page,
            "per_page": per_page,
        }
    )


@app.get("/api/quotes/<int:quote_id>")
def get_quote(quote_id):
    db = get_db()
    row = db.execute("SELECT * FROM quotes WHERE id = ?", (quote_id,)).fetchone()
    if not row:
        return jsonify({"error": "Quote not found"}), 404
    return jsonify(row_to_dict(row))


@app.post("/api/quotes")
def create_quote():
    data = request.get_json(silent=True) or {}
    text = (data.get("text") or "").strip()
    author = (data.get("author") or "").strip()
    category = (data.get("category") or "").strip()
    if not text or not author or not category:
        return (
            jsonify({"error": "text, author, and category are required"}),
            400,
        )
    db = get_db()
    cur = db.execute(
        "INSERT INTO quotes (text, author, category) VALUES (?, ?, ?)",
        (text, author, category),
    )
    db.commit()
    row = db.execute("SELECT * FROM quotes WHERE id = ?", (cur.lastrowid,)).fetchone()
    return jsonify(row_to_dict(row)), 201


@app.delete("/api/quotes/<int:quote_id>")
def delete_quote(quote_id):
    db = get_db()
    cur = db.execute("DELETE FROM quotes WHERE id = ?", (quote_id,))
    db.commit()
    if cur.rowcount == 0:
        return jsonify({"error": "Quote not found"}), 404
    return "", 204


init_db()

if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
