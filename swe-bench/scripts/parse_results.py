#!/usr/bin/env python3
"""Parse and compare SWE-bench evaluation results.

Usage:
    python3 parse_results.py <report.json>                    # Single report
    python3 parse_results.py <think_report> <nonthink_report> # Compare two
"""
import json
import sys
from collections import defaultdict

def parse_report(path):
    with open(path) as f:
        return json.load(f)

def project_breakdown(report):
    """Break down results by project."""
    projects = defaultdict(lambda: {"resolved": 0, "total": 0})
    for iid in report.get("submitted_ids", []):
        proj = iid.rsplit("-", 1)[0]
        projects[proj]["total"] += 1
    for iid in report.get("resolved_ids", []):
        proj = iid.rsplit("-", 1)[0]
        projects[proj]["resolved"] += 1
    return dict(projects)

def print_single(report, label=""):
    total = report["total_instances"]
    resolved = report["resolved_instances"]
    pct = 100 * resolved / total if total else 0
    print(f"\n{'=' * 50}")
    if label:
        print(f"  {label}")
    print(f"{'=' * 50}")
    print(f"  Resolved:      {resolved}/{total} ({pct:.1f}%)")
    print(f"  Unresolved:    {report.get('unresolved_instances', 0)}")
    print(f"  Empty patches: {report.get('empty_patch_instances', 0)}")
    print(f"  Errors:        {report.get('error_instances', 0)}")
    print(f"  Completed:     {report.get('completed_instances', 0)}")

    breakdown = project_breakdown(report)
    print(f"\n  {'Project':<40} {'Resolved':>10} {'Rate':>8}")
    print(f"  {'-'*58}")
    for proj in sorted(breakdown.keys()):
        d = breakdown[proj]
        rate = 100 * d["resolved"] / d["total"] if d["total"] else 0
        print(f"  {proj:<40} {d['resolved']:>4}/{d['total']:<4}  {rate:>6.1f}%")

def print_comparison(r1, r2, label1="Think", label2="Non-Think"):
    s1 = set(r1.get("resolved_ids", []))
    s2 = set(r2.get("resolved_ids", []))
    both = s1 & s2
    only1 = s1 - s2
    only2 = s2 - s1
    neither = set(r1.get("submitted_ids", [])) - s1 - s2

    print(f"\n{'=' * 50}")
    print(f"  Comparison: {label1} vs {label2}")
    print(f"{'=' * 50}")
    print(f"  {label1}:     {len(s1)}/500 ({100*len(s1)/500:.1f}%)")
    print(f"  {label2}: {len(s2)}/500 ({100*len(s2)/500:.1f}%)")
    print(f"  Both resolved:        {len(both)}")
    print(f"  Only {label1}:          {len(only1)}")
    print(f"  Only {label2}:      {len(only2)}")
    print(f"  Neither:              {len(neither)}")
    print(f"  Union:                {len(s1 | s2)}/500 ({100*len(s1|s2)/500:.1f}%)")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 parse_results.py <report.json> [report2.json]")
        sys.exit(1)

    r1 = parse_report(sys.argv[1])
    print_single(r1, sys.argv[1])

    if len(sys.argv) >= 3:
        r2 = parse_report(sys.argv[2])
        print_single(r2, sys.argv[2])
        print_comparison(r1, r2)
