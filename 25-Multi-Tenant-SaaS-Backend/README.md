# Multi-Tenant SaaS Backend

A compact **FastAPI** + **SQLite** backend that demonstrates **multi-tenant SaaS** patterns: shared database with **row-level isolation** by `tenant_id`, **tenant middleware** driven by the `X-Tenant-ID` header, and **plan-based limits** for users and projects.

## Multi-tenancy patterns

- **Shared schema, tenant column**: All tenant-owned rows (`tenant_users`, `projects`) include `tenant_id`. Queries for tenant APIs always filter by the resolved tenant so data never crosses tenants by accident.
- **Request-scoped tenant**: Middleware reads `X-Tenant-ID` (numeric tenant primary key), loads an **active** tenant from the database, and attaches it to `request.state`. Route handlers use a FastAPI dependency that requires this tenant for scoped endpoints.
- **Plan enforcement**: Before creating users or projects, the service compares current counts to the tenant’s plan limits. Enterprise uses `null` limits to mean unlimited.

## Features

- CRUD for **tenants** (slug uniqueness, soft deactivate via `is_active`, delete cascades users and projects).
- **Users** and **projects** scoped per tenant via header + SQL filters.
- **Subscription** summary: plan, limits, usage, illustrative monthly price.
- **Admin-style** aggregate stats and per-tenant usage (no auth layer in this demo—add API keys or JWT in production).

## Plan limits

| Plan        | Max users | Max projects |
|------------|-----------|--------------|
| free       | 5         | 3            |
| starter    | 25        | 10           |
| pro        | 100       | 50           |
| enterprise | unlimited | unlimited    |

Illustrative **MRR** prices used only for `/api/admin/stats` (USD/month per tenant): free `$0`, starter `$29`, pro `$99`, enterprise `$499`.

## Tech stack

- Python 3.11+
- FastAPI
- Uvicorn
- SQLAlchemy 2.x
- SQLite (`saas.db` file in the project directory)

## Installation

```bash
cd 25-Multi-Tenant-SaaS-Backend
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run (port 8000)

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Or:

```bash
python main.py
```

Open interactive docs: [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs)

## API overview

**`X-Tenant-ID`**: integer **tenant `id`** (from `POST /api/tenants` response or tenant list), not the slug. Required for all `/api/users`, `/api/projects`, `/api/subscription`, and `/api/subscription/plan` routes.

### Tenants

Create a tenant:

```bash
curl -s -X POST http://127.0.0.1:8000/api/tenants ^
  -H "Content-Type: application/json" ^
  -d "{\"name\": \"Acme Corp\", \"slug\": \"acme\", \"plan\": \"starter\"}"
```

List tenants:

```bash
curl -s http://127.0.0.1:8000/api/tenants
```

Get / update / delete by slug:

```bash
curl -s http://127.0.0.1:8000/api/tenants/acme
curl -s -X PUT http://127.0.0.1:8000/api/tenants/acme ^
  -H "Content-Type: application/json" ^
  -d "{\"plan\": \"pro\"}"
curl -s -X DELETE http://127.0.0.1:8000/api/tenants/acme
```

### Users (tenant-scoped)

Replace `1` with your tenant id.

```bash
curl -s -X POST http://127.0.0.1:8000/api/users ^
  -H "Content-Type: application/json" ^
  -H "X-Tenant-ID: 1" ^
  -d "{\"username\": \"jdoe\", \"email\": \"j@example.com\", \"role\": \"admin\"}"

curl -s http://127.0.0.1:8000/api/users -H "X-Tenant-ID: 1"
```

### Projects (tenant-scoped)

```bash
curl -s -X POST http://127.0.0.1:8000/api/projects ^
  -H "Content-Type: application/json" ^
  -H "X-Tenant-ID: 1" ^
  -d "{\"name\": \"Website\", \"description\": \"Marketing site\"}"

curl -s http://127.0.0.1:8000/api/projects -H "X-Tenant-ID: 1"
curl -s http://127.0.0.1:8000/api/projects/1 -H "X-Tenant-ID: 1"
```

### Plans & subscription

```bash
curl -s http://127.0.0.1:8000/api/plans
curl -s http://127.0.0.1:8000/api/subscription -H "X-Tenant-ID: 1"
curl -s -X PUT http://127.0.0.1:8000/api/subscription/plan ^
  -H "Content-Type: application/json" ^
  -H "X-Tenant-ID: 1" ^
  -d "{\"plan\": \"enterprise\"}"
```

### Admin

```bash
curl -s http://127.0.0.1:8000/api/admin/stats
curl -s http://127.0.0.1:8000/api/admin/tenants/acme/usage
```

On **Linux/macOS**, replace `^` line continuations with `\` and use single-quoted JSON where convenient.

## Project structure

```
25-Multi-Tenant-SaaS-Backend/
├── main.py           # App, models, middleware, routes
├── requirements.txt
├── README.md
└── saas.db           # Created on first run (SQLite)
```

## Production notes

This sample omits **authentication**, **authorization**, and **admin protection**. Add JWT or API keys, restrict admin routes, use PostgreSQL with connection pooling, and consider migrations (Alembic) for schema changes.
