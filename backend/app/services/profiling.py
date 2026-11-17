"""Read an uploaded CSV/XLSX into pandas and summarise it as JSON-safe data."""

import json
from pathlib import Path
from typing import Any

import pandas as pd


def read_table(path: Path, file_type: str) -> pd.DataFrame:
    if file_type == "csv":
        # shortcut: comma-separated only, upgrade to delimiter sniffing if users upload ;/tab files
        df = pd.read_csv(path, encoding_errors="replace")
    else:
        # shortcut: first sheet only, upgrade if users need to pick a sheet
        df = pd.read_excel(path, sheet_name=0)
    df.columns = [str(c) for c in df.columns]
    return df


def profile_frame(df: pd.DataFrame, sample_rows: int) -> dict[str, Any]:
    # to_json turns NaN/NaT into null and timestamps into ISO strings, so the result fits JSONB.
    stats = json.loads(df.describe(include="all").to_json(date_format="iso"))
    columns = [
        {
            "name": name,
            "dtype": str(df[name].dtype),
            "nulls": int(df[name].isna().sum()),
            "unique": int(df[name].nunique()),
            "stats": {k: v for k, v in stats.get(name, {}).items() if v is not None},
        }
        for name in df.columns
    ]
    sample = json.loads(df.head(sample_rows).to_json(orient="records", date_format="iso"))
    return {
        "row_count": len(df),
        "column_count": len(df.columns),
        "columns": columns,
        "sample": sample,
    }


def profile_file(path: Path, file_type: str, sample_rows: int) -> dict[str, Any]:
    df = read_table(path, file_type)
    if df.columns.empty:
        raise ValueError("no columns found")
    return profile_frame(df, sample_rows)
