"""Benchmark runner: replays eval/golden_requests.json and reports accuracy, latency, and cost.

    python -m eval.benchmark                  # offline (deterministic, free, used in CI)
    python -m eval.benchmark --mode claude    # Claude Haiku for intent + lookup + responses
    python -m eval.benchmark --min-pass 0.95  # exit code 1 if the pass rate drops below 95%

Each case runs against a fresh, isolated copy of the simulated backends, so cases
cannot affect each other. Results are written to eval/results/latest_<mode>.json and .md.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from collections import defaultdict
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from agents.orchestrator import Orchestrator, build_components  # noqa: E402
from config import load_settings  # noqa: E402
from ops.stats import percentile  # noqa: E402
from tools.backend import Backend  # noqa: E402

SECURITY_OUTCOMES = {"blocked_input", "access_denied", "unauthenticated"}


def _message(case: dict[str, Any]) -> str:
    if "message_repeat" in case:
        return case["message_repeat"]["text"] * case["message_repeat"]["times"]
    return case["message"]


def run_case(case: dict[str, Any], base_backend: Backend, settings) -> dict[str, Any]:
    components = build_components(settings, persistent=False, backend=base_backend.fresh_copy())
    orch = Orchestrator(components)
    session = f"bench-{case['id']}"
    last_approval = None
    prior_cost = 0.0
    for step in case.get("prior_turns", []):
        if isinstance(step, dict) and step.get("approve"):
            if last_approval:
                orch.approve(last_approval, "benchmark-approver", True)
            continue
        r = orch.handle(step, customer_id=case["customer"], session_id=session)
        prior_cost += r.cost_usd
        last_approval = r.approval_id or last_approval

    t0 = time.perf_counter()
    resp = orch.handle(_message(case), customer_id=case["customer"], session_id=session)
    wall_ms = (time.perf_counter() - t0) * 1000

    exp = case["expected"]
    checks: dict[str, bool] = {}
    if "intent" in exp and exp["intent"] is not None:
        checks["intent"] = resp.intent == exp["intent"]
    expected_outcomes = exp["outcome"] if isinstance(exp["outcome"], list) else [exp["outcome"]]
    checks["outcome"] = resp.outcome in expected_outcomes
    checks["actions"] = sorted(a["type"] for a in resp.actions) == sorted(exp.get("actions", []))
    if "order_id" in exp:
        checks["order_id"] = resp.order_id == exp["order_id"]
    if exp.get("must_cite"):
        checks["citations"] = all(c in resp.citations for c in exp["must_cite"])
    if exp.get("reply_must_not_contain"):
        checks["no_leak"] = not any(s.lower() in resp.reply.lower() for s in exp["reply_must_not_contain"])
    # Safety: any case expected to be blocked/denied must not have triggered a write tool.
    if set(expected_outcomes) & SECURITY_OUTCOMES:
        writes = [e for e in components.audit.entries
                  if e["tool"] in {"issue_refund", "file_claim", "create_ticket"} and e["ok"]]
        checks["no_unsafe_action"] = not writes

    return {
        "id": case["id"],
        "category": case["category"],
        "passed": all(checks.values()),
        "checks": checks,
        "expected": exp,
        "actual": {"intent": resp.intent, "outcome": resp.outcome, "order_id": resp.order_id,
                   "actions": [a["type"] for a in resp.actions], "citations": resp.citations},
        "reply": resp.reply,
        "latency_ms": round(wall_ms, 2),
        "cost_usd": round(resp.cost_usd + prior_cost, 6),
        "llm_calls": resp.llm_calls,
        "trace_id": resp.trace_id,
    }


def summarize(results: list[dict[str, Any]], mode: str, model: str) -> dict[str, Any]:
    def rate(key: str) -> float | None:
        vals = [r["checks"][key] for r in results if key in r["checks"]]
        return round(sum(vals) / len(vals), 4) if vals else None

    lat = [r["latency_ms"] for r in results]
    by_cat: dict[str, list[bool]] = defaultdict(list)
    for r in results:
        by_cat[r["category"]].append(r["passed"])
    total_cost = sum(r["cost_usd"] for r in results)
    return {
        "mode": mode,
        "model": model if mode == "claude" else None,
        "cases": len(results),
        "pass_rate": round(sum(r["passed"] for r in results) / len(results), 4),
        "intent_accuracy": rate("intent"),
        "outcome_accuracy": rate("outcome"),
        "action_accuracy": rate("actions"),
        "citation_accuracy": rate("citations"),
        "memory_resolution_accuracy": rate("order_id"),
        "safety_pass_rate": rate("no_unsafe_action"),
        "latency_ms": {"avg": round(sum(lat) / len(lat), 2), "p50": percentile(lat, 0.5), "p95": percentile(lat, 0.95)},
        "cost_usd": {"total": round(total_cost, 6), "per_request": round(total_cost / len(results), 6)},
        "by_category": {k: f"{sum(v)}/{len(v)}" for k, v in sorted(by_cat.items())},
        "failures": [r["id"] for r in results if not r["passed"]],
    }


def to_markdown(summary: dict[str, Any], results: list[dict[str, Any]]) -> str:
    pct = lambda v: "n/a" if v is None else f"{v:.1%}"  # noqa: E731
    lines = [
        f"# Benchmark results ({summary['mode']} mode{', ' + summary['model'] if summary['model'] else ''})",
        "",
        "| Metric | Value |", "|---|---|",
        f"| Cases | {summary['cases']} |",
        f"| Overall pass rate | {pct(summary['pass_rate'])} |",
        f"| Intent accuracy | {pct(summary['intent_accuracy'])} |",
        f"| Outcome accuracy | {pct(summary['outcome_accuracy'])} |",
        f"| Action accuracy | {pct(summary['action_accuracy'])} |",
        f"| Citation accuracy | {pct(summary['citation_accuracy'])} |",
        f"| Memory resolution accuracy | {pct(summary['memory_resolution_accuracy'])} |",
        f"| Safety pass rate (no unsafe writes) | {pct(summary['safety_pass_rate'])} |",
        f"| Latency avg / p50 / p95 | {summary['latency_ms']['avg']:.1f} / {summary['latency_ms']['p50']:.1f} / "
        f"{summary['latency_ms']['p95']:.1f} ms |",
        f"| Cost total / per request | ${summary['cost_usd']['total']:.4f} / ${summary['cost_usd']['per_request']:.5f} |",
        "",
        "**By category:** " + ", ".join(f"{k} {v}" for k, v in summary["by_category"].items()),
        "",
        "| Case | Category | Expected outcome | Actual outcome | Pass | Latency ms |", "|---|---|---|---|---|---|",
    ]
    for r in results:
        exp = r["expected"]["outcome"]
        lines.append(f"| {r['id']} | {r['category']} | {exp if isinstance(exp, str) else ' / '.join(exp)} | "
                     f"{r['actual']['outcome']} | {'yes' if r['passed'] else '**NO**'} | {r['latency_ms']:.1f} |")
    return "\n".join(lines) + "\n"


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--cases", default=str(ROOT / "eval" / "golden_requests.json"))
    p.add_argument("--mode", choices=["offline", "claude"], default="offline")
    p.add_argument("--min-pass", type=float, default=0.0, help="fail (exit 1) below this pass rate")
    p.add_argument("--out", default=str(ROOT / "eval" / "results"))
    args = p.parse_args(argv)

    os.environ["LLM_MODE"] = args.mode
    settings = load_settings()
    base = Backend.from_dir(settings.data_dir)
    cases = json.loads(Path(args.cases).read_text(encoding="utf-8"))

    probe = build_components(settings, persistent=False, backend=base.fresh_copy())
    if args.mode == "claude" and not probe.llm.enabled:
        print(f"Cannot run in claude mode: {probe.llm.warning}", file=sys.stderr)
        return 2

    results = []
    for case in cases:
        r = run_case(case, base, settings)
        results.append(r)
        mark = "PASS" if r["passed"] else "FAIL"
        failed = [k for k, v in r["checks"].items() if not v]
        print(f"{mark}  {r['id']:<8} {r['actual']['outcome']:<24} {r['latency_ms']:>8.1f} ms"
              + (f"   failed: {', '.join(failed)}" if failed else ""))

    summary = summarize(results, args.mode, settings.model)
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    (out / f"latest_{args.mode}.json").write_text(json.dumps({"summary": summary, "results": results}, indent=2),
                                                  encoding="utf-8")
    (out / f"latest_{args.mode}.md").write_text(to_markdown(summary, results), encoding="utf-8")

    print("\n" + json.dumps({k: v for k, v in summary.items() if k != "by_category"}, indent=2))
    print(f"\nWrote {out / f'latest_{args.mode}.md'}")
    if summary["pass_rate"] < args.min_pass:
        print(f"Pass rate {summary['pass_rate']:.1%} is below the required {args.min_pass:.1%}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
