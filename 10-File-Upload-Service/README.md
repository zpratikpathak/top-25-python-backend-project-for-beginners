# File Upload Service

A small REST API for uploading files with validation, persistent storage on disk, and metadata in SQLite. Built with FastAPI and SQLAlchemy.

## Features

- Multipart uploads with extension-based type checks
- 10MB maximum file size (enforced while streaming)
- UUID-based filenames on disk; original name kept in metadata
- Paginated listing, per-file metadata, download, and delete (disk + database)
- Storage statistics: totals and counts by category (image, PDF, text)

## Supported file types

| Category | Extensions |
|----------|------------|
| Images | `.jpg`, `.jpeg`, `.png`, `.gif`, `.webp`, `.bmp` |
| PDF | `.pdf` |
| Text | `.txt`, `.md`, `.csv` |

**Maximum upload size:** 10MB per file.

## Tech stack

- Python 3.10+
- [FastAPI](https://fastapi.tiangolo.com/)
- [Uvicorn](https://www.uvicorn.org/)
- [SQLAlchemy](https://www.sqlalchemy.org/) with SQLite
- [python-multipart](https://github.com/Kludex/python-multipart) for form uploads

## Installation

```bash
cd 10-File-Upload-Service
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
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

API base URL: `http://localhost:8000`  
Interactive docs: `http://localhost:8000/docs`

On first start, the app creates the `uploads/` directory and `files.db` if they do not exist.

## API endpoints

### Upload a file

```bash
curl -X POST "http://localhost:8000/api/files/upload" ^
  -F "file=@C:\path\to\document.pdf"
```

(On Unix/macOS, use `\` line continuation instead of `^`, or a single line.)

### List files (pagination)

```bash
curl "http://localhost:8000/api/files?skip=0&limit=10"
```

### Get file metadata

```bash
curl "http://localhost:8000/api/files/1"
```

### Download file

```bash
curl -OJ "http://localhost:8000/api/files/1/download"
```

(`-OJ` uses the server-suggested filename where supported.)

### Delete file

```bash
curl -X DELETE "http://localhost:8000/api/files/1"
```

### Storage stats

```bash
curl "http://localhost:8000/api/files/stats"
```

## Project structure

```
10-File-Upload-Service/
├── main.py              # FastAPI app, routes, SQLAlchemy models
├── requirements.txt     # Python dependencies
├── README.md            # This file
├── uploads/             # Created at runtime; stored files (gitignored recommended)
└── files.db             # SQLite database (created at runtime)
```

## Metadata fields

| Field | Description |
|-------|-------------|
| `id` | Integer primary key |
| `original_filename` | Name provided by the client |
| `stored_filename` | UUID + original extension on disk |
| `file_size` | Size in bytes |
| `content_type` | Client-reported MIME type (if any) |
| `upload_date` | UTC timestamp |
