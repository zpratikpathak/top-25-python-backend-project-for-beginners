# Basic Auth System (FastAPI + SQLite + JWT)

REST API for user registration, login, JWT access and refresh tokens, profile management, and password changes. Passwords are hashed with bcrypt; tokens are signed with HS256 via `python-jose`.

## Features

- User registration with unique username and email
- Login returning access token (default 30 minutes) and refresh token (default 7 days)
- Refresh endpoint to obtain a new access token
- Protected profile read/update (`GET` / `PUT` `/api/auth/me`)
- Protected password change with current password verification
- SQLite persistence via SQLAlchemy

## Tech stack

- [FastAPI](https://fastapi.tiangolo.com/)
- [Uvicorn](https://www.uvicorn.org/)
- [SQLAlchemy](https://www.sqlalchemy.org/) 2.x
- [python-jose](https://github.com/mpdavis/python-jose) (JWT)
- [passlib](https://passlib.readthedocs.io/) with bcrypt
- [python-dotenv](https://pypi.org/project/python-dotenv/)

## Installation

```bash
cd 07-Basic-Auth-System
python -m venv .venv
# Windows
.\.venv\Scripts\activate
# macOS / Linux
source .venv/bin/activate

pip install -r requirements.txt
```

Optional environment variables (defaults shown):

| Variable | Description | Default |
|----------|-------------|---------|
| `SECRET_KEY` | JWT signing secret | `dev-secret-change-in-production` |
| `DATABASE_URL` | SQLAlchemy URL | `sqlite:///./app.db` |
| `ACCESS_TOKEN_EXPIRE_MINUTES` | Access token lifetime | `30` |
| `REFRESH_TOKEN_EXPIRE_DAYS` | Refresh token lifetime | `7` |

Create a `.env` file in the project root if you want to override these values.

## Run the server

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Interactive docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

## API overview

| Method | Path | Auth | Description |
|--------|------|------|-------------|
| POST | `/api/auth/register` | No | Create account |
| POST | `/api/auth/login` | No | Get access + refresh tokens |
| POST | `/api/auth/refresh` | No (refresh token in body) | New access token |
| GET | `/api/auth/me` | Bearer access token | Current user |
| PUT | `/api/auth/me` | Bearer access token | Update username/email |
| POST | `/api/auth/change-password` | Bearer access token | Change password |

Protected routes expect header: `Authorization: Bearer <access_token>`.

## Auth flow with curl

Replace host/port if needed. On Windows PowerShell you can use `Invoke-RestMethod` similarly.

### 1. Register

```bash
curl -s -X POST http://127.0.0.1:8000/api/auth/register \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"alice\",\"email\":\"alice@example.com\",\"password\":\"secretpass1\"}"
```

### 2. Login (save tokens)

```bash
curl -s -X POST http://127.0.0.1:8000/api/auth/login \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"alice\",\"password\":\"secretpass1\"}"
```

Example response:

```json
{
  "access_token": "<jwt>",
  "refresh_token": "<jwt>",
  "token_type": "bearer"
}
```

Export for the next steps (bash):

```bash
export TOKEN="<paste access_token>"
export REFRESH="<paste refresh_token>"
```

### 3. Call a protected route

```bash
curl -s http://127.0.0.1:8000/api/auth/me \
  -H "Authorization: Bearer $TOKEN"
```

### 4. Refresh access token

```bash
curl -s -X POST http://127.0.0.1:8000/api/auth/refresh \
  -H "Content-Type: application/json" \
  -d "{\"refresh_token\":\"$REFRESH\"}"
```

Use the new `access_token` for subsequent `Authorization` headers.

### 5. Update profile (optional)

```bash
curl -s -X PUT http://127.0.0.1:8000/api/auth/me \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"username\":\"alice2\",\"email\":\"new@example.com\"}"
```

Omit fields you do not want to change (`username` and `email` are optional in the body).

### 6. Change password (optional)

```bash
curl -s -X POST http://127.0.0.1:8000/api/auth/change-password \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d "{\"old_password\":\"secretpass1\",\"new_password\":\"newsecretpass1\"}"
```

Returns `204 No Content` on success. Log in again with the new password.

## Project structure

```
07-Basic-Auth-System/
├── main.py           # FastAPI app, models, routes, JWT helpers
├── requirements.txt  # Python dependencies
├── README.md         # This file
└── app.db            # SQLite file (created on first run)
```

## Security notes

Use a strong `SECRET_KEY` in production and keep it private. Treat refresh tokens like credentials; prefer HTTPS in deployment and consider token rotation or revocation for production systems.
