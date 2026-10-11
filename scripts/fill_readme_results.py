"""Fill the Claude columns of the README results table from the saved benchmark files.

    python scripts/fill_readme_results.py

Reads eval/results/latest_claude.json and eval/results/holdout/latest_claude.json
(written by `python -m eval.benchmark --mode claude ...`) and replaces the
GOLDEN_* / HOLDOUT_* placeholders in README.md. Safe to run more than once:
if the placeholders are already filled, it re-fills the table rows in place.
"""
from __future__ import annotations

import json
import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
README = ROOT / "README.md"
SOURCES = {"GOLDEN": ROOT / "eval/results/latest_claude.json",
           "HOLDOUT": ROOT / "eval/results/holdout/latest_claude.json"}


def pct(v: float | None) -> str:
    if v is None:
        return "n/a"
    text = f"{v * 100:.1f}%"
    return text.replace(".0%", "%")


def values(summary: dict) -> dict[str, str]:
    lat = summary["latency_ms"]
    return {
        "PASS": f"**{pct(summary['pass_rate'])}**",
        "INTENT": pct(summary["intent_accuracy"]),
        "OUTCOME": pct(summary["outcome_accuracy"]),
        "SAFETY": f"**{pct(summary['safety_pass_rate'])}**",
        "LAT": f"{lat['p50'] / 1000:.1f} / {lat['p95'] / 1000:.1f} s",
        "COST": f"${summary['cost_usd']['per_request']:.5f}",
    }


def main() -> int:
    text = README.read_text(encoding="utf-8")
    filled = []
    for prefix, path in SOURCES.items():
        if not path.exists():
            print(f"skip {prefix.lower()}: {path.relative_to(ROOT)} not found (run the Claude benchmark first)")
            continue
        summary = json.loads(path.read_text(encoding="utf-8"))["summary"]
        for key, value in values(summary).items():
            text = text.replace(f"{prefix}_{key}", value)
        filled.append(f"{prefix.lower()}: pass {pct(summary['pass_rate'])}, safety {pct(summary['safety_pass_rate'])}, "
                      f"cost/request ${summary['cost_usd']['per_request']:.5f}")
    README.write_text(text, encoding="utf-8")
    left = sorted(set(re.findall(r"(?:GOLDEN|HOLDOUT)_[A-Z]+", text)))
    for line in filled:
        print("filled", line)
    print("README.md updated." if not left else f"Still unfilled: {', '.join(left)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
