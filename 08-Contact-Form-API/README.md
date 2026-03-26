# Contact Form API

A small REST API that accepts contact form submissions, stores them in SQLite, and supports listing, filtering, read/unread status, and basic statistics.

## Features

- Submit contacts with required fields and email format validation
- List submissions with optional filters (`status`, `search`), and pagination
- Fetch one submission by ID
- Mark submissions as read or unread
- Delete a submission
- Aggregate counts: total, read, unread
- SQLite database file is created automatically on startup

## Tech stack

- Python 3
- Flask
- SQLite (`sqlite3` standard library)

## Installation

```bash
cd 08-Contact-Form-API
python -m venv .venv
```

Activate the virtual environment (Windows PowerShell):

```powershell
.\.venv\Scripts\Activate.ps1
```

Install dependencies:

```bash
pip install -r requirements.txt
```

## Run the server

```bash
python main.py
```

The API listens on **http://127.0.0.1:5000**. The database file is `contacts.db` in the project directory.

## API endpoints

### POST `/api/contacts`

Submit a contact. JSON body: `name`, `email`, `subject`, `message` (all required). Email must look like a valid address.

```bash
curl -s -X POST http://127.0.0.1:5000/api/contacts \
  -H "Content-Type: application/json" \
  -d "{\"name\":\"Ada Lovelace\",\"email\":\"ada@example.com\",\"subject\":\"Hello\",\"message\":\"This is a test message.\"}"
```

### GET `/api/contacts`

List contacts. Optional query parameters:

- `status` — `read` or `unread`
- `search` — matches substrings in `name`, `subject`, or `message`
- `page` — default `1`
- `per_page` — default `20`, max `100`

```bash
curl -s "http://127.0.0.1:5000/api/contacts?status=unread&page=1&per_page=10"
```

```bash
curl -s "http://127.0.0.1:5000/api/contacts?search=hello"
```

### GET `/api/contacts/<id>`

```bash
curl -s http://127.0.0.1:5000/api/contacts/1
```

### PATCH `/api/contacts/<id>/read`

```bash
curl -s -X PATCH http://127.0.0.1:5000/api/contacts/1/read
```

### PATCH `/api/contacts/<id>/unread`

```bash
curl -s -X PATCH http://127.0.0.1:5000/api/contacts/1/unread
```

### DELETE `/api/contacts/<id>`

```bash
curl -s -X DELETE http://127.0.0.1:5000/api/contacts/1
```

### GET `/api/contacts/stats`

```bash
curl -s http://127.0.0.1:5000/api/contacts/stats
```

## Project structure

```
08-Contact-Form-API/
├── main.py           # Flask app, routes, SQLite access
├── requirements.txt  # Python dependencies
├── README.md         # This file
└── contacts.db       # Created on first run (SQLite)
```
