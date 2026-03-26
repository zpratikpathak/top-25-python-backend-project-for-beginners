# Email Sending Service

A small **FastAPI** API that queues outbound email in **SQLite**, renders **Jinja2** templates, and delivers mail in the background with **aiosmtplib**. If SMTP is not configured, the service runs in **mock mode**: it logs the send, waits briefly, and marks the message as delivered—useful for local development and tests.

## Features

- Queue emails (`pending` → `sent` or `failed`) with FastAPI `BackgroundTasks`
- Plain and optional HTML bodies; MIME `multipart/alternative` when HTML is present
- Named templates stored in the database with Jinja2 `subject` and `body` strings
- List and filter emails with pagination; inspect delivery status per message
- **Mock mode** when `SMTP_HOST` is unset or `SMTP_MOCK=true` (no real SMTP)

## Tech stack

- Python 3.10+
- FastAPI, Uvicorn
- SQLAlchemy 2.x + SQLite (`emails.db`)
- aiosmtplib, Jinja2, python-dotenv

## Installation

```bash
cd 15-Email-Sending-Service
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
copy .env.example .env
```

## Run the server (port 8000)

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Or:

```bash
python main.py
```

Open interactive docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

## SMTP configuration

Set variables in `.env` (see `.env.example`):

| Variable | Description |
|----------|-------------|
| `SMTP_HOST` | Server hostname; leave empty for mock mode |
| `SMTP_PORT` | Port (default `587`) |
| `SMTP_USER` / `SMTP_PASSWORD` | Auth if required |
| `SMTP_FROM` | From address (falls back to `SMTP_USER` if empty) |
| `SMTP_USE_TLS` | `true` / `false` for STARTTLS (default `true`) |
| `SMTP_MOCK` | `true` to force mock mode even with `SMTP_HOST` set |

## API examples (curl)

**Send an email** (mock if SMTP not configured):

```bash
curl -s -X POST http://127.0.0.1:8000/api/emails/send ^
  -H "Content-Type: application/json" ^
  -d "{\"to\":\"user@example.com\",\"subject\":\"Hello\",\"body\":\"Plain text\",\"html_body\":\"<p>Hi</p>\"}"
```

**Create a template** (Jinja2 placeholders, e.g. `{{ name }}`):

```bash
curl -s -X POST http://127.0.0.1:8000/api/templates ^
  -H "Content-Type: application/json" ^
  -d "{\"name\":\"welcome\",\"subject_template\":\"Hi {{ name }}\",\"body_template\":\"Hello {{ name }}, welcome aboard.\"}"
```

**Send using a template**:

```bash
curl -s -X POST http://127.0.0.1:8000/api/emails/send-template ^
  -H "Content-Type: application/json" ^
  -d "{\"to\":\"user@example.com\",\"template_name\":\"welcome\",\"variables\":{\"name\":\"Ada\"}}"
```

**List emails** (pagination + optional status filter):

```bash
curl -s "http://127.0.0.1:8000/api/emails?skip=0&limit=10&status=sent"
```

**Get one email**:

```bash
curl -s http://127.0.0.1:8000/api/emails/1
```

**List templates**:

```bash
curl -s http://127.0.0.1:8000/api/templates
```

**Get template + preview** (preview uses default Jinja2 undefineds; missing variables appear empty):

```bash
curl -s http://127.0.0.1:8000/api/templates/welcome
```

**Delete a template**:

```bash
curl -s -X DELETE http://127.0.0.1:8000/api/templates/welcome
```

On Linux/macOS, replace `^` line continuations with `\`.

## Project structure

```
15-Email-Sending-Service/
├── main.py           # App, models, routes, background delivery
├── requirements.txt
├── .env.example
├── README.md
└── emails.db         # Created on first run (SQLite)
```

## Notes

- Sending is asynchronous after the HTTP response: new rows start as `pending`, then move to `sent` or `failed`.
- Template sends validate variables with strict Jinja2 rules; missing keys return `400` at send time.
- Real SMTP failures store a short error message on the email row and set `status` to `failed`.
