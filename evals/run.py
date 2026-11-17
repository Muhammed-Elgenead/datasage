"""Run the eval cases through the real agent (real LLM, real sandbox) and write a report.

    uv run --project backend python -m evals.run                # all cases
    uv run --project backend python -m evals.run --only sales   # ids starting with "sales"

Each case gets a fresh session folder with copies of its files, exactly as uploads are
stored. The agent runs directly (no API, no database), so the evals measure the agent.
Reports go to docs/evals/<time>-<model>.{json,md}; the Markdown compares with the
previous JSON report, so regressions show up case by case.

Needs: Docker running with the sandbox image (scripts/sandbox.ps1 build) and LLM settings
in .env. This calls the real API and costs money.
"""

import argparse
import asyncio
import json
import shutil
import subprocess
import sys
import time
import uuid
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))  # the backend's `app` package

from app.agents.loop import AgentState, Limits, run_agent  # noqa: E402
from app.agents.providers import LLMError, LLMNotConfigured, create_provider  # noqa: E402
from app.core.config import Settings, get_settings  # noqa: E402
from app.sandbox.client import SandboxUnavailable, run_code  # noqa: E402
from app.services.profiling import profile_file  # noqa: E402
from app.services.questions import profile_text, sql_tables  # noqa: E402
from evals.scoring import score  # noqa: E402

EVALS = ROOT / "evals"
REPORTS = ROOT / "docs" / "evals"


def model_name(settings: Settings) -> str:
    return settings.gemini_model if settings.llm_provider == "gemini" else settings.anthropic_model


async def run_case(
    case: dict[str, Any], settings: Settings, provider: Any, profiles: dict[str, dict]
) -> dict[str, Any]:
    session_id = uuid.uuid4()
    folder = settings.data_dir / str(session_id)
    folder.mkdir(parents=True)
    datasets = []
    for name in case["files"]:
        stored = f"{uuid.uuid4()}{Path(name).suffix}"
        shutil.copyfile(EVALS / "datasets" / name, folder / stored)
        datasets.append(SimpleNamespace(filename=name, stored_name=stored, profile=profiles[name]))
    tables = sql_tables(datasets)  # type: ignore[arg-type]

    async def run(code: str, language: str):  # type: ignore[no-untyped-def]
        return await run_code(settings, session_id, code, tables if language == "sql" else None)

    state = AgentState()
    error = None
    started = time.monotonic()
    try:
        limits = Limits(settings.agent_max_iterations, settings.agent_max_code_errors)
        profile = profile_text(datasets, tables)  # type: ignore[arg-type]
        await run_agent(provider, limits, case["question"], profile, run, state, mode=case["mode"])
    except (LLMError, SandboxUnavailable) as exc:
        error = f"{type(exc).__name__}: {exc}"
    except Exception as exc:  # one broken case must not end the whole run
        error = f"unexpected {type(exc).__name__}: {exc}"
    latency = time.monotonic() - started

    ok_runs = [r for r in state.runs if r.result.status == "ok"]
    shown = ok_runs[-1] if ok_runs else (state.runs[-1] if state.runs else None)
    shown_tables = [t.model_dump() for t in shown.result.tables] if shown else []
    value_ok, reason = score(case["expected"], state.answer or "", shown_tables)
    chart_ok = bool(shown and shown.result.charts) if case["chart"] else None
    status = "error" if error else state.status  # "error": the API or sandbox failed
    passed = status == "ok" and value_ok and chart_ok is not False
    if error:
        reason = error
    elif passed:
        reason = ""
    elif not reason or status != "ok":
        reason = f"status {status}" if status != "ok" else "no chart"
    return {
        "id": case["id"],
        "question": case["question"],
        "mode": case["mode"],
        "passed": passed,
        "reason": reason,
        "status": status,
        "languages": [r.language for r in state.runs],
        "attempts": len(state.runs),
        "failed_runs": sum(r.result.status != "ok" for r in state.runs),
        "chart_ok": chart_ok,
        "latency_s": round(latency, 1),
        "input_tokens": state.usage.get("input_tokens", 0),
        "output_tokens": state.usage.get("output_tokens", 0),
        "answer": (state.answer or "")[:500],
        "code": shown.code if shown else None,
    }


def summarize(results: list[dict[str, Any]]) -> dict[str, Any]:
    n = len(results)
    by_dataset: dict[str, list[bool]] = {}
    for r in results:
        by_dataset.setdefault(r["id"].split("-")[0], []).append(r["passed"])
    return {
        "cases": n,
        "passed": sum(r["passed"] for r in results),
        "pass_rate": round(sum(r["passed"] for r in results) / n, 3) if n else 0,
        "by_dataset": {k: f"{sum(v)}/{len(v)}" for k, v in by_dataset.items()},
        "errored": sum(r["status"] == "error" for r in results),  # API/sandbox, not the agent
        "self_corrected": sum(r["failed_runs"] > 0 and r["passed"] for r in results),
        "failed_runs": sum(r["failed_runs"] for r in results),
        "median_latency_s": sorted(r["latency_s"] for r in results)[n // 2] if n else 0,
        "input_tokens": sum(r["input_tokens"] for r in results),
        "output_tokens": sum(r["output_tokens"] for r in results),
    }


def compare(current: dict[str, Any], previous: dict[str, Any] | None) -> list[str]:
    if previous is None:
        return ["No previous report to compare with."]
    before = {r["id"]: r["passed"] for r in previous["results"]}
    now = {r["id"]: r["passed"] for r in current["results"]}
    # A case that hit an API or sandbox error says nothing about the agent: don't compare it.
    errored = {
        r["id"] for run in (previous, current) for r in run["results"] if r["status"] == "error"
    }
    common = (before.keys() & now.keys()) - errored
    worse = sorted(i for i in common if before[i] and not now[i])
    better = sorted(i for i in common if not before[i] and now[i])
    p, c = previous["summary"], current["summary"]
    lines = [
        f"Compared with `{previous['name']}` ({previous['meta']['model']}): "
        f"pass rate {p['pass_rate']:.0%} → {c['pass_rate']:.0%} "
        f"({c['pass_rate'] - p['pass_rate']:+.0%}).",
        f"- **Regressions** (passed before, fail now): {', '.join(worse) or 'none'}",
        f"- **Fixed** (failed before, pass now): {', '.join(better) or 'none'}",
    ]
    if errored:
        lines.append(f"- Not comparable (API or sandbox errors): {', '.join(sorted(errored))}")
    if now.keys() != before.keys():
        added, removed = len(now.keys() - before.keys()), len(before.keys() - now.keys())
        lines.append(f"- Cases differ: {added} new, {removed} removed.")
    return lines


def markdown(report: dict[str, Any], comparison: list[str]) -> str:
    s, m = report["summary"], report["meta"]
    out = [
        f"# Eval report {report['name']}",
        "",
        f"Model `{m['model']}` ({m['provider']}), commit `{m['commit']}`, {m['started']}.",
        "",
        f"**{s['passed']}/{s['cases']} passed ({s['pass_rate']:.0%}).** "
        f"By dataset: {', '.join(f'{k} {v}' for k, v in s['by_dataset'].items())}. "
        f"API/sandbox errors: {s.get('errored', 0)}. "
        f"Self-corrected to a pass: {s['self_corrected']}. Failed code runs: {s['failed_runs']}. "
        f"Median latency {s['median_latency_s']} s. Tokens: {s['input_tokens']:,} in, "
        f"{s['output_tokens']:,} out.",
        "",
        *comparison,
        "",
        "| Case | Pass | Status | Runs (failed) | Lang | Latency | Reason |",
        "|---|---|---|---|---|---|---|",
    ]
    for r in report["results"]:
        langs = "+".join(sorted(set(r["languages"]))) or "-"
        out.append(
            f"| {r['id']} | {'✅' if r['passed'] else '❌'} | {r['status']} | "
            f"{r['attempts']} ({r['failed_runs']}) | {langs} | {r['latency_s']} s | "
            f"{r['reason'].replace('|', '/')} |"
        )
    return "\n".join(out) + "\n"


def sandbox_ready(settings: Settings) -> bool:
    import shlex

    cmd = [*shlex.split(settings.sandbox_docker), "image", "inspect", settings.sandbox_image]
    try:
        return subprocess.run(cmd, capture_output=True, timeout=60, check=False).returncode == 0
    except (OSError, subprocess.TimeoutExpired):
        return False


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--only", nargs="*", default=[], help="case id prefixes to run")
    parser.add_argument("--concurrency", type=int, default=2)
    args = parser.parse_args()
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]  # cp1252 consoles

    started = datetime.now(UTC)
    stamp = started.strftime("%Y-%m-%dT%H%M")
    settings = get_settings()
    settings = settings.model_copy(update={"data_dir": ROOT / ".tmp" / "evals" / stamp})
    try:
        provider = create_provider(settings)
    except LLMNotConfigured as exc:
        print(exc)
        return 2
    if not sandbox_ready(settings):
        print("The sandbox isn't ready: start Docker and run scripts/sandbox.ps1 build.")
        return 2

    cases = json.loads((EVALS / "cases.json").read_text(encoding="utf-8"))
    cases = [c for c in cases if not args.only or c["id"].startswith(tuple(args.only))]
    files = {f for c in cases for f in c["files"]}
    profiles = {
        f: profile_file(
            EVALS / "datasets" / f, Path(f).suffix.lstrip("."), settings.profile_sample_rows
        )
        for f in files
    }

    gate = asyncio.Semaphore(args.concurrency)

    async def one(case: dict[str, Any]) -> dict[str, Any]:
        async with gate:
            result = await run_case(case, settings, provider, profiles)
            if "rate limit" in result["reason"]:  # free-tier quotas recover within a minute
                await asyncio.sleep(60)
                result = await run_case(case, settings, provider, profiles)
            print(f"{'PASS' if result['passed'] else 'FAIL'} {result['id']:<14} "
                  f"{result['latency_s']:>5} s  {result['reason']}", flush=True)  # fmt: skip
            return result

    results = await asyncio.gather(*(one(c) for c in cases))
    git = await asyncio.to_thread(
        subprocess.run,
        ["git", "rev-parse", "--short", "HEAD"],
        capture_output=True,
        text=True,
        cwd=ROOT,
        check=False,
    )
    commit = git.stdout.strip()
    name = f"{stamp}-{model_name(settings).replace('/', '_')}"
    report = {
        "name": name,
        "meta": {
            "started": started.isoformat(timespec="seconds"),
            "provider": settings.llm_provider,
            "model": model_name(settings),
            "commit": commit,
            "only": args.only,
        },
        "summary": summarize(results),
        "results": results,
    }
    REPORTS.mkdir(parents=True, exist_ok=True)
    earlier = sorted(p for p in REPORTS.glob("*.json") if p.stem < name)
    previous = json.loads(earlier[-1].read_text(encoding="utf-8")) if earlier else None
    (REPORTS / f"{name}.json").write_text(json.dumps(report, indent=2) + "\n", encoding="utf-8")
    md = markdown(report, compare(report, previous))
    (REPORTS / f"{name}.md").write_text(md, encoding="utf-8")
    shutil.rmtree(settings.data_dir, ignore_errors=True)
    print("\n" + md.split("\n| Case")[0])
    print(f"Report: docs/evals/{name}.md")
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
