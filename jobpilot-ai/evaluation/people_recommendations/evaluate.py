#!/usr/bin/env python3
"""Offline, identifier-free evaluation for People Who Can Help."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DEFAULT_BENCHMARK = ROOT / "fixtures" / "benchmark-v1.json"
RESULTS = ROOT / "results"
TARGETS = {
    "recruiter_precision_at_3": 0.85,
    "referral_precision_at_5": 0.80,
    "current_employment_accuracy": 0.90,
    "verified_work_email_accuracy": 0.95,
    "unsupported_claim_rate_max": 0.02,
}


def precision(items: list[dict], k: int) -> float | None:
    selected = items[:k]
    return sum(bool(item["relevant"]) for item in selected) / len(selected) if selected else None


def mean(values: list[float | None]) -> float | None:
    present = [value for value in values if value is not None]
    return sum(present) / len(present) if present else None


def evaluate(data: dict) -> dict:
    cases = data.get("cases") or []
    category_items = {name: [] for name in ("recruiter", "manager", "referral")}
    employment: list[dict] = []
    emails: list[dict] = []
    duplicates = 0
    predictions = 0
    unsupported = 0
    stale = 0
    no_result = 0
    category_no_result = {name: 0 for name in category_items}
    parent_mismatches = 0
    reviewed_company_matches = 0
    enrichment_attempts = 0
    enrichment_matches = 0
    credits = 0
    successful_jobs = 0
    per_case_precision = {
        "recruiter_1": [],
        "recruiter_3": [],
        "manager_1": [],
        "referral_3": [],
        "referral_5": [],
    }
    calibration: list[tuple[float, float]] = []
    false_positives: list[dict] = []
    for case in cases:
        for category, target in category_items.items():
            rows = (case.get("categories") or {}).get(category) or []
            category_no_result[category] += int(not rows)
            target.extend(rows)
            predictions += len(rows)
            for row in rows:
                calibration.append((float(row.get("confidence", 0)), float(bool(row["relevant"]))))
                if not row["relevant"]:
                    false_positives.append({"case_id": case["case_id"], "category": category, "id": row["id"]})
                if "parent_subsidiary_mismatch" in row:
                    reviewed_company_matches += 1
                    parent_mismatches += int(bool(row["parent_subsidiary_mismatch"]))
        per_case_precision["recruiter_1"].append(
            precision((case.get("categories") or {}).get("recruiter") or [], 1)
        )
        per_case_precision["recruiter_3"].append(
            precision((case.get("categories") or {}).get("recruiter") or [], 3)
        )
        per_case_precision["manager_1"].append(
            precision((case.get("categories") or {}).get("manager") or [], 1)
        )
        per_case_precision["referral_3"].append(
            precision((case.get("categories") or {}).get("referral") or [], 3)
        )
        per_case_precision["referral_5"].append(
            precision((case.get("categories") or {}).get("referral") or [], 5)
        )
        funnel = case.get("funnel") or {}
        enrichment_attempts += int(funnel.get("enrichment_attempts") or 0)
        enrichment_matches += int(funnel.get("enrichment_matches") or 0)
        credits += int(funnel.get("credits_consumed") or 0)
        successful_jobs += int(
            any((case.get("categories") or {}).get(category) for category in category_items)
        )
        employment.extend(case.get("employment") or [])
        emails.extend(case.get("emails") or [])
        duplicates += len(case.get("duplicates") or [])
        unsupported += len(case.get("unsupported_claims") or [])
        stale += sum(bool(row.get("stale")) for row in case.get("employment") or [])
        no_result += int(bool(case.get("no_reliable_result_returned")))
    current_correct = sum(
        row.get("predicted_current") == row.get("actually_current") for row in employment
    )
    verified = [row for row in emails if row.get("status") == "verified"]
    email_correct = sum(bool(row.get("actually_deliverable_work_email")) for row in verified)
    metrics = {
        "recruiter_precision_at_1": mean(per_case_precision["recruiter_1"]),
        "recruiter_precision_at_3": mean(per_case_precision["recruiter_3"]),
        "recruiter_no_result_rate": category_no_result["recruiter"] / len(cases) if cases else None,
        "potential_hiring_manager_precision_at_1": mean(per_case_precision["manager_1"]),
        "manager_no_result_rate": category_no_result["manager"] / len(cases) if cases else None,
        "referral_precision_at_3": mean(per_case_precision["referral_3"]),
        "referral_precision_at_5": mean(per_case_precision["referral_5"]),
        "current_employment_accuracy": current_correct / len(employment) if employment else None,
        "duplicate_rate": duplicates / predictions if predictions else 0.0,
        "no_reliable_result_rate": no_result / len(cases) if cases else None,
        "stale_record_rate": stale / len(employment) if employment else None,
        "verified_work_email_accuracy": email_correct / len(verified) if verified else None,
        "confidence_calibration_error": (
            sum(abs(confidence - outcome) for confidence, outcome in calibration) / len(calibration)
            if calibration else None
        ),
        "unsupported_claim_rate": unsupported / max(1, predictions),
        "parent_subsidiary_mismatch_rate": (
            parent_mismatches / reviewed_company_matches if reviewed_company_matches else None
        ),
        "enrichment_success_rate": (
            enrichment_matches / enrichment_attempts if enrichment_attempts else None
        ),
        "credits_per_successful_job_discovery": (
            credits / successful_jobs if successful_jobs else None
        ),
    }
    failures = []
    for metric, target in TARGETS.items():
        if metric.endswith("_max"):
            continue
        if metrics.get(metric) is None or metrics[metric] < target:
            failures.append(f"{metric} < {target}")
    if metrics["unsupported_claim_rate"] >= TARGETS["unsupported_claim_rate_max"]:
        failures.append(
            f"unsupported_claim_rate >= {TARGETS['unsupported_claim_rate_max']}"
        )
    return {
        "schema_version": data.get("schema_version"),
        "case_count": len(cases),
        "metrics": metrics,
        "targets": TARGETS,
        "passed": not failures,
        "failures": failures,
        "false_positive_reports": false_positives,
    }


def markdown(result: dict) -> str:
    lines = [
        "# People recommendation evaluation",
        "",
        f"Cases: {result['case_count']}",
        f"Release thresholds passed: {'yes' if result['passed'] else 'no'}",
        "",
        "| Metric | Result |",
        "|---|---:|",
    ]
    for name, value in result["metrics"].items():
        lines.append(f"| {name} | {'n/a' if value is None else f'{value:.4f}'} |")
    if result["failures"]:
        lines += ["", "## Failures", "", *[f"- {item}" for item in result["failures"]]]
    lines += ["", "Synthetic/redacted fixtures only; these results are not a public accuracy claim.", ""]
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--benchmark", type=Path, default=DEFAULT_BENCHMARK)
    parser.add_argument("--allow-incomplete", action="store_true")
    args = parser.parse_args()
    data = json.loads(args.benchmark.read_text(encoding="utf-8"))
    if data.get("schema_version") != "1.0" or not isinstance(data.get("cases"), list):
        raise SystemExit("Invalid benchmark schema/version")
    result = evaluate(data)
    RESULTS.mkdir(exist_ok=True)
    (RESULTS / "latest.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    (RESULTS / "latest.md").write_text(markdown(result), encoding="utf-8")
    print(markdown(result))
    allow = args.allow_incomplete or os.getenv("PEOPLE_EVAL_ALLOW_INCOMPLETE", "").lower() == "true"
    return 0 if result["passed"] or allow else 1


if __name__ == "__main__":
    raise SystemExit(main())
