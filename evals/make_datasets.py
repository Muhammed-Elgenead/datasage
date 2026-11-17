"""Generate the eval datasets and cases with their correct answers.

Deterministic (fixed seeds), so the files and answers only change when this script does.
The answers are computed here with careful pandas (each date format parsed explicitly,
prices cleaned, nulls handled), never by the agent.

    uv run --project backend python evals/make_datasets.py
"""

import json
import random
from datetime import date, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

HERE = Path(__file__).parent
DATA = HERE / "datasets"


def num(value: float, tol: float = 0.01, rel: float = 0.001) -> dict:
    return {"type": "number", "value": round(float(value), 4), "abs_tol": tol, "rel_tol": rel}


def rows(mapping: dict, tol: float = 0.01, rel: float = 0.001) -> dict:
    return {
        "type": "rows",
        "rows": {str(k): round(float(v), 4) for k, v in mapping.items()},
        "abs_tol": tol,
        "rel_tol": rel,
    }


def text(value: str) -> dict:
    return {"type": "text", "value": value}


# --- sales: text prices and mixed date formats ---------------------------------------------


def sales() -> tuple[list[str], list[dict]]:
    rng = random.Random(7)
    prices = {"Widget": 12.5, "Gadget": 30.0, "Doohickey": 7.25}
    records = []
    for i in range(240):
        d = date(2026, 1, 1) + timedelta(days=rng.randint(0, 180))
        records.append(
            {
                "order_date": d.isoformat() if i % 3 else d.strftime("%d/%m/%Y"),
                "region": rng.choice(["North", "South", "East", "West"]),
                "product": (p := rng.choice(list(prices))),
                "units": rng.randint(1, 20),
                "unit_price": f"${prices[p]:.2f}",
                "_date": d,
            }
        )
    df = pd.DataFrame(records)
    df.drop(columns="_date").to_csv(DATA / "sales.csv", index=False)

    df["revenue"] = df.units * df.unit_price.str.lstrip("$").astype(float)
    df["month"] = pd.to_datetime(df._date).dt.strftime("%Y-%m")
    by_product = df.groupby("product").revenue.sum()
    by_month = df.groupby("month").revenue.sum()
    west_march = df[(df.region == "West") & (df.month == "2026-03")].revenue.sum()
    cases = [
        ("How many orders are in the file?", num(len(df))),
        ("How many units were sold in total?", num(df.units.sum())),
        ("What is the total revenue (units times unit price)?", num(df.revenue.sum())),
        ("What is the total revenue (units times unit price) for each product?", rows(by_product)),
        ("Which product brought in the most revenue?", text(by_product.idxmax())),
        ("How many orders came from each region?", rows(df.region.value_counts())),
        ("What is the average number of units per order?", num(df.units.mean(), rel=0.005)),
        (
            "What was the total revenue per month? Write each month as YYYY-MM.",
            rows(by_month),
        ),
        ("Which month had the highest revenue? Write it as YYYY-MM.", text(by_month.idxmax())),
        (
            "What was the West region's revenue in March 2026?",
            num(west_march),
        ),
        (
            "Show a bar chart of total units sold per region.",
            rows(df.groupby("region").units.sum()),
            {"chart": True},
        ),
        (
            "How many orders were placed for each product?",
            rows(df["product"].value_counts()),
            {"mode": "sql"},
        ),
    ]
    return ["sales.csv"], cases


# --- employees: a clean Excel file -----------------------------------------------------------


def employees() -> tuple[list[str], list[dict]]:
    rng = np.random.default_rng(11)
    first = ["Ada", "Lin", "Omar", "Sara", "Ken", "Mia", "Ivan", "Zoe", "Raj", "Eva", "Tom", "Nia"]
    last = ["Berg", "Chen", "Diaz", "Eko", "Ford", "Gale", "Hart", "Ito", "Jain", "Kova"]
    depts = ["Engineering", "Sales", "Support", "Finance"]
    base = {"Engineering": 85000, "Sales": 62000, "Support": 48000, "Finance": 70000}
    n = 60
    dept = rng.choice(depts, n, p=[0.35, 0.3, 0.2, 0.15])
    df = pd.DataFrame(
        {
            "name": [f"{first[i % 12]} {last[(i * 7) % 10]}" for i in range(n)],
            "department": dept,
            "salary": [int(base[d] + rng.normal(0, 9000)) // 100 * 100 for d in dept],
            "hire_date": [
                pd.Timestamp("2016-01-01") + pd.Timedelta(days=int(x))
                for x in rng.integers(0, 365 * 9, n)
            ],
            "rating": rng.integers(1, 6, n),
            "remote": rng.choice(["yes", "no"], n, p=[0.4, 0.6]),
        }
    )
    df.loc[df.salary.idxmax(), "name"] = "Grace Hopper"  # a unique top earner
    df.to_excel(DATA / "employees.xlsx", index=False)

    year = df.hire_date.dt.year
    cases = [
        ("How many employees are there?", num(n)),
        (
            "What is the average salary in each department?",
            rows(df.groupby("department").salary.mean(), tol=1),
        ),
        ("Which department has the most employees?", text(df.department.value_counts().idxmax())),
        ("What is the median salary across all employees?", num(df.salary.median())),
        ("How many employees were hired in 2023?", num((year == 2023).sum())),
        (
            "What percentage of employees work remotely?",
            num((df.remote == "yes").mean() * 100, tol=0.1),
        ),
        ("Who is the highest-paid employee?", text("Grace Hopper")),
        (
            "What is the average rating of employees hired before 2020?",
            num(df[year < 2020].rating.mean(), tol=0.01, rel=0.005),
        ),
        (
            "Chart the number of hires per year.",
            rows(year.value_counts()),
            {"chart": True},
        ),
        (
            "What is the total salary cost of each department?",
            rows(df.groupby("department").salary.sum()),
            {"mode": "sql"},
        ),
    ]
    return ["employees.xlsx"], cases


# --- weather: missing values -------------------------------------------------------------------


def weather() -> tuple[list[str], list[dict]]:
    rng = np.random.default_rng(3)
    days = pd.date_range("2026-01-01", periods=120, freq="D")
    frames = []
    for city, mean, rain in [("Oslo", 2.0, 2.5), ("Bergen", 5.0, 7.0), ("Madrid", 13.0, 1.2)]:
        temp = mean + 6 * np.sin(np.arange(120) / 120 * np.pi) + rng.normal(0, 3, 120)
        rainfall = rng.gamma(0.6, rain / 0.6, 120)
        frames.append(
            pd.DataFrame(
                {
                    "date": days,
                    "city": city,
                    "temp_c": temp.round(1),
                    "rainfall_mm": rainfall.round(1),
                }
            )
        )
    df = pd.concat(frames, ignore_index=True)
    missing = rng.choice(len(df), 18, replace=False)
    df.loc[missing, "temp_c"] = np.nan
    df["date"] = df.date.dt.strftime("%Y-%m-%d")
    df.to_csv(DATA / "weather.csv", index=False)

    hottest = df.loc[df.temp_c.idxmax()]
    cases = [
        (
            "What is the average temperature in each city?",
            rows(df.groupby("city").temp_c.mean(), tol=0.05),
        ),
        (
            "Which city had the most total rainfall?",
            text(df.groupby("city").rainfall_mm.sum().idxmax()),
        ),
        ("How many temperature readings are missing?", num(df.temp_c.isna().sum())),
        (
            "On which date was the highest temperature recorded? Write it as YYYY-MM-DD.",
            text(hottest.date),
        ),
        (
            "On how many days did Bergen get more than 10 mm of rain?",
            num(((df.city == "Bergen") & (df.rainfall_mm > 10)).sum()),
        ),
        (
            "Plot the monthly average temperature for each city as a line chart.",
            {"type": "none"},
            {"chart": True},
        ),
        (
            "What is the correlation between temperature and rainfall across all readings?",
            num(df.temp_c.corr(df.rainfall_mm), tol=0.01, rel=0.0),
        ),
    ]
    return ["weather.csv"], cases


# --- shop: two files to join -------------------------------------------------------------------


def shop() -> tuple[list[str], list[dict]]:
    rng = np.random.default_rng(5)
    countries = ["Norway", "Spain", "Japan", "Canada"]
    customers = pd.DataFrame(
        {
            "customer_id": range(1, 41),
            "name": [f"Customer {i:02d}" for i in range(1, 41)],
            "country": rng.choice(countries, 40),
            "segment": rng.choice(["Basic", "Premium"], 40, p=[0.7, 0.3]),
        }
    )
    orders = pd.DataFrame(
        {
            "order_id": range(1001, 1151),
            "customer_id": rng.choice(range(1, 36), 150),  # customers 36-40 never order
            "amount": rng.gamma(2.0, 40.0, 150).round(2),
        }
    )
    customers.to_csv(DATA / "customers.csv", index=False)
    orders.to_csv(DATA / "orders.csv", index=False)

    joined = orders.merge(customers, on="customer_id")
    spend = joined.groupby("name").amount.sum()
    cases = [
        (
            "What is the total order amount per customer country?",
            rows(joined.groupby("country").amount.sum()),
        ),
        ("Which customer has spent the most in total?", text(spend.idxmax())),
        (
            "How many customers have never placed an order?",
            num(len(set(customers.customer_id) - set(orders.customer_id))),
        ),
        (
            "How many orders were placed by each customer segment?",
            rows(joined.segment.value_counts()),
            {"mode": "sql"},
        ),
        (
            "What is the average order amount for Premium customers?",
            num(joined[joined.segment == "Premium"].amount.mean()),
        ),
    ]
    return ["customers.csv", "orders.csv"], cases


def main() -> None:
    DATA.mkdir(exist_ok=True)
    cases = []
    for group in (sales, employees, weather, shop):
        files, items = group()
        for i, (question, expected, *extra) in enumerate(items, start=1):
            opts = extra[0] if extra else {}
            cases.append(
                {
                    "id": f"{group.__name__}-{i:02d}",
                    "files": files,
                    "question": question,
                    "expected": expected,
                    "chart": opts.get("chart", False),
                    "mode": opts.get("mode", "auto"),
                }
            )
    (HERE / "cases.json").write_text(json.dumps(cases, indent=2) + "\n", encoding="utf-8")
    print(f"{len(cases)} cases over {len({tuple(c['files']) for c in cases})} datasets")


if __name__ == "__main__":
    main()
