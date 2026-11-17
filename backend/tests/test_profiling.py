import json
from pathlib import Path

import pandas as pd
import pytest

from app.services.profiling import profile_file


def write(tmp_path: Path, name: str, content: str) -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


def test_csv_profile(tmp_path: Path) -> None:
    path = write(tmp_path, "a.csv", "city,sales,note\nOslo,10,x\nRome,30,\nOslo,,y\n")

    p = profile_file(path, "csv", sample_rows=2)

    assert p["row_count"] == 3
    assert p["column_count"] == 3
    cols = {c["name"]: c for c in p["columns"]}
    assert cols["sales"]["nulls"] == 1
    assert cols["sales"]["stats"]["mean"] == 20.0
    assert cols["sales"]["stats"]["max"] == 30.0
    assert cols["city"]["unique"] == 2
    assert cols["city"]["stats"]["top"] == "Oslo"
    assert cols["note"]["nulls"] == 1
    assert p["sample"] == [
        {"city": "Oslo", "sales": 10.0, "note": "x"},
        {"city": "Rome", "sales": 30.0, "note": None},
    ]
    json.dumps(p, allow_nan=False)  # JSONB-safe: no NaN anywhere


def test_xlsx_profile_with_dates_and_numeric_headers(tmp_path: Path) -> None:
    path = tmp_path / "b.xlsx"
    pd.DataFrame({"when": pd.to_datetime(["2026-01-02", "2026-03-04"]), 2026: [1.5, 2.5]}).to_excel(
        path, index=False
    )

    p = profile_file(path, "xlsx", sample_rows=5)

    assert [c["name"] for c in p["columns"]] == ["when", "2026"]
    assert p["sample"][0]["when"].startswith("2026-01-02")
    json.dumps(p, allow_nan=False)


def test_header_only_csv_has_zero_rows(tmp_path: Path) -> None:
    p = profile_file(write(tmp_path, "c.csv", "a,b\n"), "csv", sample_rows=5)
    assert (p["row_count"], p["column_count"], p["sample"]) == (0, 2, [])


def test_non_utf8_csv_is_read(tmp_path: Path) -> None:
    path = tmp_path / "d.csv"
    path.write_bytes("name\nJosé\n".encode("latin-1"))
    assert profile_file(path, "csv", sample_rows=5)["row_count"] == 1


def test_empty_xlsx_sheet_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "e.xlsx"
    pd.DataFrame().to_excel(path, index=False)
    with pytest.raises(ValueError, match="no columns"):
        profile_file(path, "xlsx", sample_rows=5)
