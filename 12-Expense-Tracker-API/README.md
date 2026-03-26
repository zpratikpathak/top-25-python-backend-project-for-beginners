# Expense Tracker API

REST API for personal expense and income tracking with SQLite persistence, category summaries, monthly breakdowns, and optional per-category monthly budgets.

## Features

- Create, list (with filters and pagination), read, update, and delete transactions
- Financial summary: total income, total expenses, and balance
- Monthly breakdown of income and expenses by category
- Aggregated totals per category across all time
- Monthly budgets per category with spent amount and remaining budget

## Tech stack

- Python 3.10+
- [FastAPI](https://fastapi.tiangolo.com/)
- [SQLAlchemy](https://www.sqlalchemy.org/) 2.x with SQLite
- [Uvicorn](https://www.uvicorn.org/) ASGI server

## Installation

```bash
cd 12-Expense-Tracker-API
python -m venv .venv
```

**Windows (PowerShell):**

```powershell
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

**macOS / Linux:**

```bash
source .venv/bin/activate
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

The SQLite file `expense_tracker.db` is created in the project directory on first run.

## API endpoints

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/transactions` | Create a transaction |
| GET | `/api/transactions` | List transactions (filters, pagination) |
| GET | `/api/transactions/{id}` | Get one transaction |
| PUT | `/api/transactions/{id}` | Update a transaction |
| DELETE | `/api/transactions/{id}` | Delete a transaction |
| GET | `/api/summary` | Totals: income, expenses, balance |
| GET | `/api/summary/monthly` | Monthly totals by category |
| GET | `/api/categories` | Categories with income/expense totals |
| POST | `/api/budgets` | Create or update a monthly category budget |
| GET | `/api/budgets` | Budgets for a month with spent vs budget |

### cURL examples

Replace `BASE=http://127.0.0.1:8000` as needed.

**Create a transaction (expense)**

```bash
curl -s -X POST "$BASE/api/transactions" ^
  -H "Content-Type: application/json" ^
  -d "{\"amount\": 45.50, \"type\": \"expense\", \"category\": \"Food\", \"description\": \"Lunch\", \"date\": \"2025-03-15\"}"
```

On bash/macOS, use single quotes for the JSON body:

```bash
curl -s -X POST "$BASE/api/transactions" \
  -H "Content-Type: application/json" \
  -d '{"amount": 1200, "type": "income", "category": "Salary", "description": "March pay", "date": "2025-03-01"}'
```

**List transactions (filters and pagination)**

```bash
curl -s "$BASE/api/transactions?type=expense&category=Food&start_date=2025-03-01&end_date=2025-03-31&skip=0&limit=20"
```

**Get one transaction**

```bash
curl -s "$BASE/api/transactions/1"
```

**Update a transaction**

```bash
curl -s -X PUT "$BASE/api/transactions/1" \
  -H "Content-Type: application/json" \
  -d '{"amount": 50.0, "description": "Lunch (updated)"}'
```

**Delete a transaction**

```bash
curl -s -X DELETE "$BASE/api/transactions/1" -w "\n%{http_code}\n"
```

**Financial summary**

```bash
curl -s "$BASE/api/summary"
```

**Monthly breakdown by category**

```bash
curl -s "$BASE/api/summary/monthly?year=2025&month=3"
```

**Categories with totals**

```bash
curl -s "$BASE/api/categories"
```

**Set or update a monthly budget** (same category + month updates the amount)

```bash
curl -s -X POST "$BASE/api/budgets" \
  -H "Content-Type: application/json" \
  -d '{"category": "Food", "amount": 500, "month": "2025-03"}'
```

**Budgets for a month (spent vs budget)**

```bash
curl -s "$BASE/api/budgets?month=2025-03"
```

## Project structure

```
12-Expense-Tracker-API/
├── main.py              # FastAPI app, models, schemas, routes
├── requirements.txt     # Python dependencies
├── README.md            # This file
└── expense_tracker.db   # SQLite database (created at runtime)
```

## Data model (summary)

- **Transaction:** `amount`, `type` (`income` | `expense`), `category`, `description`, `date`, `created_at`
- **Budget:** `category`, `amount`, `month` (`YYYY-MM`), `created_at` — unique per `(category, month)`

Spent amounts for budgets are computed from **expense** transactions in that category whose `date` falls in the budget month.
