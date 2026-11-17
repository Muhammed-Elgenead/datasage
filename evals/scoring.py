"""Deterministic scoring: does the agent's answer contain the known-correct result?

An answer is the reply text plus the tables of the run the user sees. Expected values:
  number  the value appears in the text or in a table cell (within tolerance)
  rows    for every key, some table row (or text line) has that key and the value
  text    the string appears in the text or a table cell (case-insensitive)
  none    nothing to check beyond the run succeeding (and the chart, if asked for)

shortcut: "appears somewhere" can pass by coincidence when an answer lists many numbers;
upgrade to an LLM judge if that shows up in reports.
"""

import re
from typing import Any

NUMBER = re.compile(r"-?\d[\d,]*(?:\.\d+)?")


def numbers_in(text: str) -> list[float]:
    # The pattern only matches digits, commas and one decimal part, so float() can't fail.
    return [float(m.replace(",", "")) for m in NUMBER.findall(text)]


def close(a: float, b: float, abs_tol: float, rel_tol: float) -> bool:
    return abs(a - b) <= max(abs_tol, rel_tol * abs(b))


def _cell_numbers(cell: Any) -> list[float]:
    if isinstance(cell, bool):
        return []
    if isinstance(cell, int | float):
        return [float(cell)]
    return numbers_in(str(cell)) if cell is not None else []


def _same_key(cell: Any, key: str) -> bool:
    if isinstance(cell, int | float) and not isinstance(cell, bool):
        return key.replace(".", "", 1).isdigit() and close(float(cell), float(key), 0, 0)
    # A date-like key also matches a timestamp cell ("2026-03" vs "2026-03-01T00:00:00").
    value = str(cell).strip().lower()
    return value == key.lower() or (
        bool(re.fullmatch(r"\d{4}-\d{2}", key)) and value.startswith(key)
    )


def _row_has(row: list[Any], key: str, value: float, abs_tol: float, rel_tol: float) -> bool:
    key_cells = [i for i, c in enumerate(row) if _same_key(c, key)]
    return any(
        close(n, value, abs_tol, rel_tol)
        for i, c in enumerate(row)
        if key_cells and i not in key_cells
        for n in _cell_numbers(c)
    )


def score(expected: dict[str, Any], answer: str, tables: list[dict[str, Any]]) -> tuple[bool, str]:
    """(passed, reason)."""
    kind = expected["type"]
    cells = [c for t in tables for r in t["rows"] for c in r]
    if kind == "none":
        return True, "no value to check"
    if kind == "text":
        want = expected["value"].lower()
        hit = want in answer.lower() or any(want == str(c).strip().lower() for c in cells)
        return hit, "" if hit else f"{expected['value']!r} not in the answer"

    abs_tol, rel_tol = expected["abs_tol"], expected["rel_tol"]
    if kind == "number":
        value = expected["value"]
        pool = numbers_in(answer) + [n for c in cells for n in _cell_numbers(c)]
        hit = any(close(n, value, abs_tol, rel_tol) for n in pool)
        return hit, "" if hit else f"{value:g} not found"
    if kind == "rows":
        lines = answer.lower().splitlines()
        missing = []
        for key, value in expected["rows"].items():
            in_table = any(
                _row_has(r, key, value, abs_tol, rel_tol) for t in tables for r in t["rows"]
            )
            in_text = any(
                key.lower() in line
                and any(close(n, value, abs_tol, rel_tol) for n in numbers_in(line))
                for line in lines
            )
            if not (in_table or in_text):
                missing.append(f"{key}={value:g}")
        return not missing, "" if not missing else "missing " + ", ".join(missing)
    raise ValueError(f"unknown expected type {kind!r}")
