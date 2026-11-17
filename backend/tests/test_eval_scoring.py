"""The eval scorer (evals/scoring.py): it decides pass/fail, so it needs tests of its own."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))  # repo root, for `evals`

from evals.scoring import numbers_in, score


def num(value: float, abs_tol: float = 0.01, rel_tol: float = 0.001) -> dict:
    return {"type": "number", "value": value, "abs_tol": abs_tol, "rel_tol": rel_tol}


def rows(mapping: dict, abs_tol: float = 0.01, rel_tol: float = 0.001) -> dict:
    return {"type": "rows", "rows": mapping, "abs_tol": abs_tol, "rel_tol": rel_tol}


def table(columns: list[str], data: list[list]) -> dict:
    return {"columns": columns, "rows": data}


def test_numbers_in_text() -> None:
    assert numbers_in("Revenue was $40,017.25 over 240 orders (-3.5%).") == [40017.25, 240, -3.5]


def test_number_found_in_text_or_table() -> None:
    assert score(num(40017.25), "Total revenue is $40,017.25.", [])[0]
    assert score(num(240), "Here it is.", [table(["n"], [[240]])])[0]
    assert score(num(86.92), "About 86.92 on average.", [])[0]


def test_number_tolerance() -> None:
    assert score(num(87666.6667, abs_tol=1), "Engineering averages $87,667.", [])[0]
    assert not score(num(0.42, abs_tol=0.01, rel_tol=0), "The correlation is 0.4.", [])[0]
    ok, reason = score(num(240), "There are 239 orders.", [])
    assert not ok and "240" in reason


def test_rows_in_a_table() -> None:
    t = table(["product", "revenue"], [["Gadget", 23100.0], ["Widget", 11037.5]])
    assert score(rows({"Gadget": 23100.0, "Widget": 11037.5}), "", [t])[0]
    ok, reason = score(rows({"Gadget": 23100.0, "Doohickey": 5879.75}), "", [t])
    assert not ok and "Doohickey" in reason


def test_rows_match_the_key_not_any_cell() -> None:
    # The right number in the wrong row doesn't count.
    t = table(["product", "revenue"], [["Gadget", 11037.5], ["Widget", 23100.0]])
    assert not score(rows({"Gadget": 23100.0}), "", [t])[0]


def test_rows_in_text_lines() -> None:
    answer = "Revenue per product:\nGadget: $23,100.00\nWidget: $11,037.50"
    assert score(rows({"Gadget": 23100.0, "Widget": 11037.5}), answer, [])[0]


def test_rows_with_year_and_month_keys() -> None:
    years = table(["hire_year", "count"], [[2019, 7], [2023, 4]])
    assert score(rows({"2019": 7, "2023": 4}), "", [years])[0]
    months = table(["month", "revenue"], [["2026-03-01T00:00:00.000", 6500.0]])
    assert score(rows({"2026-03": 6500.0}), "", [months])[0]


def test_text() -> None:
    assert score({"type": "text", "value": "Gadget"}, "The top product is gadget.", [])[0]
    assert score({"type": "text", "value": "2026-03-17"}, "", [table(["date"], [["2026-03-17"]])])[
        0
    ]
    assert not score({"type": "text", "value": "Bergen"}, "Oslo had the most rain.", [])[0]


def test_none() -> None:
    assert score({"type": "none"}, "", [])[0]


def _report(name: str, passed: dict[str, bool], errored: tuple[str, ...] = ()) -> dict:
    from evals.run import summarize

    results = [
        {
            "id": i,
            "passed": p,
            "status": "error" if i in errored else "ok",
            "failed_runs": 0,
            "latency_s": 1.0,
            "input_tokens": 1,
            "output_tokens": 1,
        }
        for i, p in passed.items()
    ]
    return {"name": name, "meta": {"model": "m"}, "summary": summarize(results), "results": results}


def test_comparison_lists_regressions_and_fixes() -> None:
    from evals.run import compare

    before = _report("old", {"sales-01": True, "sales-02": False, "shop-01": True})
    after = _report("new", {"sales-01": False, "sales-02": True, "shop-01": True})

    lines = compare(after, before)

    assert "67% → 67%" in lines[0]
    assert lines[1].endswith("sales-01")  # regression
    assert lines[2].endswith("sales-02")  # fixed
    assert after["summary"]["by_dataset"] == {"sales": "1/2", "shop": "1/1"}


def test_first_report_has_nothing_to_compare() -> None:
    from evals.run import compare

    assert compare(_report("new", {"a-01": True}), None) == ["No previous report to compare with."]


def test_api_errors_are_not_regressions() -> None:
    from evals.run import compare

    before = _report("old", {"a-01": True, "a-02": True})
    after = _report("new", {"a-01": False, "a-02": False}, errored=("a-02",))

    lines = compare(after, before)

    assert lines[1].endswith("a-01")  # a real regression
    assert "a-02" not in lines[1]
    assert lines[3] == "- Not comparable (API or sandbox errors): a-02"
    assert after["summary"]["errored"] == 1
