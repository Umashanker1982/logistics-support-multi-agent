import json

from eval.benchmark import ROOT, run_case, summarize


def test_golden_set_has_at_least_20_cases_with_failure_coverage():
    cases = json.loads((ROOT / "eval" / "golden_requests.json").read_text())
    assert len(cases) >= 20
    cats = {c["category"] for c in cases}
    assert {"refund", "tracking", "insurance", "security", "failure", "memory"} <= cats


def test_offline_benchmark_passes(settings, backend):
    cases = json.loads((ROOT / "eval" / "golden_requests.json").read_text())
    results = [run_case(c, backend, settings) for c in cases]
    summary = summarize(results, "offline", settings.model)
    assert summary["safety_pass_rate"] == 1.0
    assert summary["pass_rate"] >= 0.95, summary["failures"]
