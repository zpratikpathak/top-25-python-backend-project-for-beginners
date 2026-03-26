# Social Media API

A REST API for a small social network: user profiles, posts with optional images, likes, comments, follows, and a personalized feed. Built with **FastAPI**, **SQLAlchemy 2**, and **SQLite** (file `social.db`).

## Features

- User registration and profile updates with follower, following, and post counts
- Posts with optional `image_url`, list/filter by author, detail with engagement counts
- Likes with duplicate prevention (DB unique constraint + application check)
- Threaded-style comments per post with pagination
- Follow / unfollow with self-follow blocked and duplicate follow rejected
- Feed of posts from followed accounts, newest first, paginated

## Data model

| Entity   | Relationships |
|----------|----------------|
| **User** | Owns many **Posts**; many **Likes** and **Comments**; **Follow** rows as follower or followee |
| **Post** | Belongs to one **User**; has many **Likes** and **Comments** |
| **Like** | One user, one post; `(user_id, post_id)` unique |
| **Comment** | One user, one post; stores `content` and `created_at` |
| **Follow** | `follower_id` → `following_id`; pair unique; no row where follower equals followee |

## Tech stack

- Python 3.10+
- FastAPI
- Uvicorn
- SQLAlchemy 2.x (ORM, SQLite)

## Installation

```bash
cd 23-Social-Media-API
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run the server

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Or:

```bash
python main.py
```

Interactive docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

## API overview

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/users` | Create user |
| GET | `/api/users/{username}` | Profile + counts |
| PUT | `/api/users/{username}` | Update email/bio |
| POST | `/api/posts` | Create post |
| GET | `/api/posts` | List posts (`skip`, `limit`, `author`) |
| GET | `/api/posts/{id}` | Post + like/comment counts |
| DELETE | `/api/posts/{id}` | Delete post |
| POST | `/api/posts/{id}/like` | Like (`username` in JSON) |
| DELETE | `/api/posts/{id}/like?username=` | Unlike |
| GET | `/api/posts/{id}/likes` | Usernames who liked |
| POST | `/api/posts/{id}/comments` | Add comment |
| GET | `/api/posts/{id}/comments` | List comments (`skip`, `limit`) |
| DELETE | `/api/comments/{id}` | Delete comment |
| POST | `/api/users/{username}/follow` | Follow (`follower_username` in body) |
| DELETE | `/api/users/{username}/follow?follower=` | Unfollow |
| GET | `/api/users/{username}/followers` | Follower usernames |
| GET | `/api/users/{username}/following` | Following usernames |
| GET | `/api/feed/{username}` | Feed from followed users (`skip`, `limit`) |

## Example flow (curl)

Base URL: `http://127.0.0.1:8000`

**1. Create users**

```bash
curl -s -X POST http://127.0.0.1:8000/api/users -H "Content-Type: application/json" -d "{\"username\":\"alice\",\"email\":\"alice@example.com\",\"bio\":\"Hi\"}"
curl -s -X POST http://127.0.0.1:8000/api/users -H "Content-Type: application/json" -d "{\"username\":\"bob\",\"email\":\"bob@example.com\",\"bio\":\"Hey\"}"
```

**2. Bob follows Alice**

```bash
curl -s -X POST http://127.0.0.1:8000/api/users/alice/follow -H "Content-Type: application/json" -d "{\"follower_username\":\"bob\"}"
curl -s http://127.0.0.1:8000/api/users/alice/followers
curl -s http://127.0.0.1:8000/api/users/bob/following
```

**3. Alice posts; Bob likes and comments**

```bash
curl -s -X POST http://127.0.0.1:8000/api/posts -H "Content-Type: application/json" -d "{\"author_username\":\"alice\",\"content\":\"Hello world\",\"image_url\":\"https://example.com/pic.jpg\"}"
```

Assume post id `1`:

```bash
curl -s -X POST http://127.0.0.1:8000/api/posts/1/like -H "Content-Type: application/json" -d "{\"username\":\"bob\"}"
curl -s -X POST http://127.0.0.1:8000/api/posts/1/comments -H "Content-Type: application/json" -d "{\"username\":\"bob\",\"content\":\"Nice post!\"}"
curl -s http://127.0.0.1:8000/api/posts/1
curl -s http://127.0.0.1:8000/api/posts/1/likes
curl -s "http://127.0.0.1:8000/api/posts/1/comments?skip=0&limit=10"
```

**4. Bob’s feed (posts from people he follows)**

```bash
curl -s "http://127.0.0.1:8000/api/feed/bob?skip=0&limit=20"
```

**5. Unlike, unfollow, cleanup**

```bash
curl -s -X DELETE "http://127.0.0.1:8000/api/posts/1/like?username=bob"
curl -s -X DELETE "http://127.0.0.1:8000/api/users/alice/follow?follower=bob"
```

On Windows PowerShell you can use `Invoke-RestMethod` or escape JSON differently; the examples above match `curl` on Unix or Git Bash on Windows.

## Project structure

```
23-Social-Media-API/
├── main.py           # App, models, routes, DB setup
├── requirements.txt
├── README.md
└── social.db         # Created on first run (SQLite)
```
