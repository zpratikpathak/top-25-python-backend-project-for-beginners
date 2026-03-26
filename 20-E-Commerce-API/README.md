# E-Commerce API

A small REST API for products, shopping carts, and orders. Sessions are identified by `X-Session-ID` (header) or `session_id` (query parameter). Stock is checked at checkout; line prices are stored on each order item.

## Features

- Product CRUD with filters (category, price range, text search, in-stock flag) and pagination
- Per-session cart (add, update quantity, remove, clear) with live totals from current product prices
- Checkout creates an order, reserves inventory, snapshots unit prices, and empties the cart
- Order listing and detail for the same session
- Order status workflow: `pending` → `confirmed` → `shipped` → `delivered`, or `cancelled` from `pending`, `confirmed`, or `shipped`

## Tech stack

- Python 3.11+
- FastAPI
- Uvicorn
- SQLAlchemy 2.x
- SQLite (`ecommerce.db` in the project directory)

## Installation

```bash
cd 20-E-Commerce-API
python -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

## Run the server (port 8000)

```bash
uvicorn main:app --reload --host 0.0.0.0 --port 8000
```

Or:

```bash
python main.py
```

Open interactive docs at [http://127.0.0.1:8000/docs](http://127.0.0.1:8000/docs).

## API overview

| Method | Path | Description |
|--------|------|-------------|
| POST | `/api/products` | Create product |
| GET | `/api/products` | List products (filters + `skip` / `limit`) |
| GET | `/api/products/{id}` | Product detail |
| PUT | `/api/products/{id}` | Update product |
| DELETE | `/api/products/{id}` | Delete product |
| POST | `/api/cart/items` | Add to cart |
| GET | `/api/cart` | Cart + totals |
| PUT | `/api/cart/items/{product_id}` | Set line quantity |
| DELETE | `/api/cart/items/{product_id}` | Remove line |
| DELETE | `/api/cart` | Clear cart |
| POST | `/api/orders` | Checkout |
| GET | `/api/orders` | List orders for session |
| GET | `/api/orders/{id}` | Order + items |
| PATCH | `/api/orders/{id}/status` | Change status |

Cart and order routes require `X-Session-ID: <your-session>` or `?session_id=<your-session>`.

## Example flow (curl)

Set a session id once (PowerShell):

```powershell
$H = @{ "X-Session-ID" = "demo-session-1"; "Content-Type" = "application/json" }
```

### 1. Create products

```bash
curl -s -X POST http://127.0.0.1:8000/api/products -H "Content-Type: application/json" -d "{\"name\":\"Widget A\",\"description\":\"A useful widget\",\"price\":\"19.99\",\"stock\":50,\"category\":\"gadgets\"}"

curl -s -X POST http://127.0.0.1:8000/api/products -H "Content-Type: application/json" -d "{\"name\":\"Widget B\",\"description\":\"Another widget\",\"price\":\"9.50\",\"stock\":100,\"category\":\"gadgets\"}"
```

Note the returned `id` values (assume `1` and `2` below).

### 2. List / filter products

```bash
curl -s "http://127.0.0.1:8000/api/products?category=gadgets&in_stock=true&skip=0&limit=10"
```

### 3. Add to cart (with session header)

```bash
curl -s -X POST http://127.0.0.1:8000/api/cart/items -H "Content-Type: application/json" -H "X-Session-ID: demo-session-1" -d "{\"product_id\":1,\"quantity\":2}"

curl -s -X POST http://127.0.0.1:8000/api/cart/items -H "Content-Type: application/json" -H "X-Session-ID: demo-session-1" -d "{\"product_id\":2,\"quantity\":1}"
```

### 4. View cart

```bash
curl -s http://127.0.0.1:8000/api/cart -H "X-Session-ID: demo-session-1"
```

### 5. Checkout (create order)

```bash
curl -s -X POST http://127.0.0.1:8000/api/orders -H "Content-Type: application/json" -H "X-Session-ID: demo-session-1" -d "{\"shipping_address\":\"123 Main St, City, Country\"}"
```

Save the order `id` from the response (assume `1`).

### 6. Cart is empty after checkout

```bash
curl -s http://127.0.0.1:8000/api/cart -H "X-Session-ID: demo-session-1"
```

### 7. List orders and track status

```bash
curl -s http://127.0.0.1:8000/api/orders -H "X-Session-ID: demo-session-1"

curl -s http://127.0.0.1:8000/api/orders/1 -H "X-Session-ID: demo-session-1"

curl -s -X PATCH http://127.0.0.1:8000/api/orders/1/status -H "Content-Type: application/json" -H "X-Session-ID: demo-session-1" -d "{\"status\":\"confirmed\"}"

curl -s -X PATCH http://127.0.0.1:8000/api/orders/1/status -H "Content-Type: application/json" -H "X-Session-ID: demo-session-1" -d "{\"status\":\"shipped\"}"

curl -s -X PATCH http://127.0.0.1:8000/api/orders/1/status -H "Content-Type: application/json" -H "X-Session-ID: demo-session-1" -d "{\"status\":\"delivered\"}"
```

Using query param instead of header:

```bash
curl -s "http://127.0.0.1:8000/api/cart?session_id=demo-session-1"
```

## Project structure

```
20-E-Commerce-API/
├── main.py           # App, models, schemas, routes, DB setup
├── requirements.txt
├── README.md
└── ecommerce.db      # Created on first run (SQLite file)
```
