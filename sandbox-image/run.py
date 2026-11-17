"""Runs inside the sandbox container.

Reads /out/code.py, runs it with pandas/numpy/matplotlib preloaded, and writes
/out/result.json plus chart_N.png files. The backend treats everything here as untrusted
and validates it again.

The code can call `show(obj, title=None)` to return a DataFrame/Series as a table. If the
last statement is an expression, its value is shown too (a table for DataFrame/Series,
otherwise its repr).

SQL mode: when the backend writes /out/query.sql (and /out/tables.json, mapping table names
to files under /data), the query runs in DuckDB over those tables instead.
"""

import ast
import io
import json
import linecache
import os
import shutil
import sys
import time
import traceback
from contextlib import redirect_stderr, redirect_stdout
from typing import Any

OUT = os.environ.get("SANDBOX_OUT", "/out")
MAX_CHARS = int(os.environ.get("MAX_OUTPUT_CHARS", "20000"))
MAX_ROWS = int(os.environ.get("MAX_TABLE_ROWS", "200"))
MAX_TABLES = int(os.environ.get("MAX_TABLES", "10"))
MAX_CHARTS = int(os.environ.get("MAX_CHARTS", "5"))

# Font cache built into the image; copy it so matplotlib doesn't rebuild it on every run.
if os.path.isdir("/opt/mplconfig"):
    shutil.copytree("/opt/mplconfig", os.environ["MPLCONFIGDIR"], dirs_exist_ok=True)

import matplotlib  # noqa: E402

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402


class CappedText(io.StringIO):
    """Keeps at most MAX_CHARS, so a print loop can't eat the memory limit."""

    truncated = False

    def write(self, s: str) -> int:
        room = MAX_CHARS - self.tell()
        if len(s) > room:
            self.truncated = True
        if room > 0:
            super().write(s[:room])
        return len(s)


def to_table(obj: Any, title: str | None) -> dict[str, Any]:
    df = obj.to_frame() if isinstance(obj, pd.Series) else obj
    if not isinstance(df.index, pd.RangeIndex):
        df = df.reset_index()
    df = df.copy()
    df.columns = [str(c) for c in df.columns]
    head = df.head(MAX_ROWS)
    rows = json.loads(head.to_json(orient="values", date_format="iso", default_handler=str))
    return {
        "title": title,
        "columns": list(df.columns),
        "rows": rows,
        "total_rows": len(df),
        "truncated": len(df) > len(head),
    }


def read_table(path: str) -> pd.DataFrame:
    """Same reading rules as the backend's profiling (app/services/profiling.py)."""
    if path.endswith(".csv"):
        df = pd.read_csv(path, encoding_errors="replace")
    else:
        df = pd.read_excel(path, sheet_name=0)
    df.columns = [str(c) for c in df.columns]
    return df


def run_sql(query: str, show: Any) -> str | None:
    """Run DuckDB SQL over the session's files, each loaded as a table."""
    import duckdb

    con = duckdb.connect()
    with open(os.path.join(OUT, "tables.json"), encoding="utf-8") as f:
        for name, path in json.load(f).items():
            con.register(name, read_table(path))
    # The query sees only these tables: no file or network access, and it can't undo that.
    con.execute("SET enable_external_access = false")
    con.execute("SET lock_configuration = true")
    relation = con.sql(query)
    if relation is None:  # a statement without a result set
        return "Statement executed."
    show(relation.df())
    return None


def main() -> None:
    sql_mode = os.path.exists(os.path.join(OUT, "query.sql"))
    with open(os.path.join(OUT, "query.sql" if sql_mode else "code.py"), encoding="utf-8") as f:
        source = f.read()
    linecache.cache["<code>"] = (len(source), None, source.splitlines(True), "<code>")

    tables: list[dict[str, Any]] = []
    notes: list[str] = []

    def show(obj: Any, title: str | None = None) -> None:
        if not isinstance(obj, pd.DataFrame | pd.Series):
            raise TypeError(f"show() takes a DataFrame or Series, not {type(obj).__name__}")
        if len(tables) >= MAX_TABLES:
            notes.append(f"only the first {MAX_TABLES} tables are kept")
            return
        tables.append(to_table(obj, title))

    namespace: dict[str, Any] = {
        "__name__": "__main__",
        "pd": pd,
        "np": np,
        "plt": plt,
        "show": show,
        "DATA_DIR": "/data",
    }
    stdout, stderr = CappedText(), CappedText()
    error: str | None = None
    result: str | None = None
    started = time.monotonic()

    with redirect_stdout(stdout), redirect_stderr(stderr):
        try:
            if sql_mode:
                result = run_sql(source, show)
            else:
                tree = ast.parse(source, "<code>")
                last = tree.body.pop() if tree.body and isinstance(tree.body[-1], ast.Expr) else None
                exec(compile(tree, "<code>", "exec"), namespace)
                if last is not None:
                    value = eval(compile(ast.Expression(last.value), "<code>", "eval"), namespace)
                    if isinstance(value, pd.DataFrame | pd.Series):
                        show(value)
                    elif value is not None:
                        result = repr(value)[:MAX_CHARS]
        except SystemExit:
            pass
        except BaseException as exc:
            if sql_mode:  # the runner's frames say nothing useful about a SQL error
                error = f"{type(exc).__name__}: {exc}"[-MAX_CHARS:]
            else:
                tb = exc.__traceback__
                while tb is not None and tb.tb_frame.f_code.co_filename != "<code>":
                    tb = tb.tb_next  # hide the runner's own frames
                error = "".join(traceback.format_exception(type(exc), exc, tb))[-MAX_CHARS:]

    charts: list[str] = []
    fignums = plt.get_fignums()
    for i, num in enumerate(fignums[:MAX_CHARTS], start=1):
        name = f"chart_{i}.png"
        plt.figure(num).savefig(os.path.join(OUT, name), dpi=100, bbox_inches="tight")
        charts.append(name)
    if len(fignums) > MAX_CHARTS:
        notes.append(f"only the first {MAX_CHARTS} charts are kept")
    if stdout.truncated or stderr.truncated:
        notes.append(f"output was cut to {MAX_CHARS} characters")

    with open(os.path.join(OUT, "result.json"), "w", encoding="utf-8") as f:
        json.dump(
            {
                "stdout": stdout.getvalue(),
                "stderr": stderr.getvalue(),
                "error": error,
                "result": result,
                "tables": tables,
                "charts": charts,
                "notes": notes,
                "duration_ms": int((time.monotonic() - started) * 1000),
            },
            f,
        )


if __name__ == "__main__":
    main()
    sys.exit(0)
