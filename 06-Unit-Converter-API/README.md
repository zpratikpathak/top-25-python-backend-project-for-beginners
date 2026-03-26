# Unit Converter API

A small Flask REST API that converts numeric values between units. Conversions use a base-unit factor table (except temperature, which uses standard formulas). Volume units follow **US customary** liquid measures.

## Features

- Convert between units via query parameters
- List supported categories and the units in each category
- Clear JSON error responses for invalid input, unknown categories, or unsupported unit pairs

## Tech stack

- Python 3
- Flask 3.x

## Installation

```bash
cd 06-Unit-Converter-API
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

The API listens on **http://127.0.0.1:5000** (port 5000).

## API endpoints

### `GET /api/convert`

Query parameters: `value`, `from`, `to` (all required).

**Example — length (meters to miles):**

```bash
curl "http://127.0.0.1:5000/api/convert?value=1000&from=meter&to=mile"
```

**Example — temperature:**

```bash
curl "http://127.0.0.1:5000/api/convert?value=32&from=fahrenheit&to=celsius"
```

**Example — error (bad unit pair):**

```bash
curl "http://127.0.0.1:5000/api/convert?value=1&from=meter&to=kilogram"
```

### `GET /api/categories`

Returns all supported categories.

```bash
curl "http://127.0.0.1:5000/api/categories"
```

### `GET /api/units/{category}`

Returns unit names for `length`, `weight`, `temperature`, `volume`, `speed`, or `time`.

```bash
curl "http://127.0.0.1:5000/api/units/length"
```

```bash
curl "http://127.0.0.1:5000/api/units/temperature"
```

## Supported units

| Category     | Units |
|-------------|--------|
| **length**  | `meter`, `kilometer`, `mile`, `yard`, `foot`, `inch`, `centimeter`, `millimeter` |
| **weight**  | `kilogram`, `gram`, `milligram`, `pound`, `ounce`, `ton` (metric tonne, 1000 kg) |
| **temperature** | `celsius`, `fahrenheit`, `kelvin` |
| **volume**  | `liter`, `milliliter`, `gallon`, `quart`, `pint`, `cup`, `fluid_ounce` (US) |
| **speed**   | `mph`, `kph`, `knot`, `mps` |
| **time**    | `second`, `minute`, `hour`, `day`, `week` |

Unit names in requests are case-insensitive. Spaces are treated like underscores (e.g. `fluid ounce` → `fluid_ounce`).

## Project structure

```
06-Unit-Converter-API/
├── main.py           # Flask app and conversion logic
├── requirements.txt  # Python dependencies
└── README.md         # This file
```
