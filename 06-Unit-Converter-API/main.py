from flask import Flask, jsonify, request

app = Flask(__name__)

LENGTH_TO_METER = {
    "meter": 1.0,
    "kilometer": 1000.0,
    "mile": 1609.344,
    "yard": 0.9144,
    "foot": 0.3048,
    "inch": 0.0254,
    "centimeter": 0.01,
    "millimeter": 0.001,
}

WEIGHT_TO_KG = {
    "kilogram": 1.0,
    "gram": 0.001,
    "milligram": 1e-6,
    "pound": 0.45359237,
    "ounce": 0.028349523125,
    "ton": 1000.0,
}

VOLUME_TO_LITER = {
    "liter": 1.0,
    "milliliter": 0.001,
    "gallon": 3.785411784,
    "quart": 0.946352946,
    "pint": 0.473176473,
    "cup": 0.2365882365,
    "fluid_ounce": 0.0295735295625,
}

SPEED_TO_MPS = {
    "mps": 1.0,
    "kph": 1000.0 / 3600.0,
    "mph": 1609.344 / 3600.0,
    "knot": 1852.0 / 3600.0,
}

TIME_TO_SECOND = {
    "second": 1.0,
    "minute": 60.0,
    "hour": 3600.0,
    "day": 86400.0,
    "week": 604800.0,
}

TEMPERATURE_UNITS = frozenset({"celsius", "fahrenheit", "kelvin"})

CATEGORY_MAP = {
    "length": LENGTH_TO_METER,
    "weight": WEIGHT_TO_KG,
    "volume": VOLUME_TO_LITER,
    "speed": SPEED_TO_MPS,
    "time": TIME_TO_SECOND,
}


def normalize_unit(name):
    if not name:
        return ""
    return str(name).strip().lower().replace(" ", "_")


def to_celsius(value, unit):
    if unit == "celsius":
        return value
    if unit == "fahrenheit":
        return (value - 32.0) * 5.0 / 9.0
    if unit == "kelvin":
        return value - 273.15
    raise ValueError(f"unsupported temperature unit: {unit}")


def from_celsius(value, unit):
    if unit == "celsius":
        return value
    if unit == "fahrenheit":
        return value * 9.0 / 5.0 + 32.0
    if unit == "kelvin":
        return value + 273.15
    raise ValueError(f"unsupported temperature unit: {unit}")


def convert_temperature(value, from_u, to_u):
    c = to_celsius(value, from_u)
    return from_celsius(c, to_u)


def resolve_category(from_u, to_u):
    if from_u in TEMPERATURE_UNITS and to_u in TEMPERATURE_UNITS:
        return "temperature"
    for cat, table in CATEGORY_MAP.items():
        if from_u in table and to_u in table:
            return cat
    return None


def convert_factor(value, from_u, to_u, table):
    if from_u not in table:
        raise ValueError(f"unsupported unit in category: {from_u}")
    if to_u not in table:
        raise ValueError(f"unsupported unit in category: {to_u}")
    base = value * table[from_u]
    return base / table[to_u]


@app.route("/api/categories", methods=["GET"])
def list_categories():
    cats = list(CATEGORY_MAP.keys()) + ["temperature"]
    cats.sort()
    return jsonify({"categories": cats})


@app.route("/api/units/<category>", methods=["GET"])
def list_units(category):
    cat = normalize_unit(category)
    if cat == "temperature":
        return jsonify({"category": "temperature", "units": sorted(TEMPERATURE_UNITS)})
    if cat not in CATEGORY_MAP:
        return jsonify({"error": f"unknown category: {category}"}), 404
    return jsonify({"category": cat, "units": sorted(CATEGORY_MAP[cat].keys())})


@app.route("/api/convert", methods=["GET"])
def convert():
    value_raw = request.args.get("value")
    from_u = normalize_unit(request.args.get("from", ""))
    to_u = normalize_unit(request.args.get("to", ""))

    if value_raw is None or value_raw == "":
        return jsonify({"error": "missing required query parameter: value"}), 400
    if not from_u:
        return jsonify({"error": "missing required query parameter: from"}), 400
    if not to_u:
        return jsonify({"error": "missing required query parameter: to"}), 400

    try:
        value = float(value_raw)
    except (TypeError, ValueError):
        return jsonify({"error": "value must be a number"}), 400

    if from_u == to_u:
        category = resolve_category(from_u, to_u)
        if category is None:
            return jsonify({"error": f"unsupported unit: {from_u}"}), 400
        return jsonify(
            {
                "value": value,
                "from": from_u,
                "to": to_u,
                "result": value,
                "category": category,
            }
        )

    category = resolve_category(from_u, to_u)
    if category is None:
        return jsonify(
            {
                "error": "unsupported unit pair or units from different categories",
                "from": from_u,
                "to": to_u,
            }
        ), 400

    try:
        if category == "temperature":
            result = convert_temperature(value, from_u, to_u)
        else:
            result = convert_factor(value, from_u, to_u, CATEGORY_MAP[category])
    except ValueError as e:
        return jsonify({"error": str(e)}), 400

    return jsonify(
        {
            "value": value,
            "from": from_u,
            "to": to_u,
            "result": result,
            "category": category,
        }
    )


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=5000, debug=True)
