#!/usr/bin/env python3
"""Parse and compare SWE-bench evaluation results.

Usage:
    python3 parse_results.py <report.json>                              # Single report
    python3 parse_results.py <report1> <report2>                        # Pairwise compare
    python3 parse_results.py <report1> <report2> ... <reportN>          # Pass@k ensemble (N>=3)

Pass@k ensemble: prints per-run scores, pairwise overlap matrix,
N-way intersect (resolved by all), N-way union (Pass@k score),
and per-run unique contributions.
"""
import json
import os
import sys
from collections import defaultdict
from itertools import combinations

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

def short_label(path):
    return os.path.basename(path).replace(".json", "")

def print_single(report, label=""):
    total = report["total_instances"]
    resolved = report["resolved_instances"]
    pct = 100 * resolved / total if total else 0
    print(f"\n{'=' * 60}")
    if label:
        print(f"  {label}")
    print(f"{'=' * 60}")
    print(f"  Resolved:      {resolved}/{total} ({pct:.2f}%)")
    print(f"  Unresolved:    {report.get('unresolved_instances', 0)}")
    print(f"  Empty patches: {report.get('empty_patch_instances', 0)}")
    print(f"  Errors:        {report.get('error_instances', 0)}")
    print(f"  Completed:     {report.get('completed_instances', 0)}")

    breakdown = project_breakdown(report)
    print(f"\n  {'Project':<40} {'Resolved':>10} {'Rate':>8}")
    print(f"  {'-'*60}")
    for proj in sorted(breakdown.keys()):
        d = breakdown[proj]
        rate = 100 * d["resolved"] / d["total"] if d["total"] else 0
        print(f"  {proj:<40} {d['resolved']:>4}/{d['total']:<4}  {rate:>6.1f}%")

def print_pairwise(r1, r2, label1, label2):
    s1 = set(r1.get("resolved_ids", []))
    s2 = set(r2.get("resolved_ids", []))
    submitted = set(r1.get("submitted_ids", [])) | set(r2.get("submitted_ids", []))
    both = s1 & s2
    only1 = s1 - s2
    only2 = s2 - s1
    neither = submitted - s1 - s2
    total = len(submitted)

    print(f"\n{'=' * 60}")
    print(f"  Comparison: {label1} vs {label2}")
    print(f"{'=' * 60}")
    print(f"  {label1}:               {len(s1)}/{total} ({100*len(s1)/total:.2f}%)")
    print(f"  {label2}:               {len(s2)}/{total} ({100*len(s2)/total:.2f}%)")
    print(f"  Both resolved:        {len(both)}")
    print(f"  Only {label1}:        {len(only1)}")
    print(f"  Only {label2}:        {len(only2)}")
    print(f"  Neither:              {len(neither)}")
    union = s1 | s2
    print(f"  Pass@2 union:         {len(union)}/{total} ({100*len(union)/total:.2f}%)")

def print_passk(reports, labels):
    """Print Pass@k ensemble metrics for k = len(reports) >= 3."""
    sets = [set(r.get("resolved_ids", [])) for r in reports]
    submitted = set()
    for r in reports:
        submitted |= set(r.get("submitted_ids", []))
    total = len(submitted)
    k = len(sets)

    print(f"\n{'=' * 60}")
    print(f"  Pass@{k} ensemble across {k} runs")
    print(f"{'=' * 60}")
    print(f"  Per-run scores:")
    for label, s in zip(labels, sets):
        print(f"    {label:<48} {len(s):>4}/{total} ({100*len(s)/total:>5.2f}%)")

    # N-way intersect, union, jointly-unresolved
    intersect_all = set.intersection(*sets) if sets else set()
    union_all = set.union(*sets) if sets else set()
    none_resolved = submitted - union_all
    print()
    print(f"  Resolved by ALL {k} runs:    {len(intersect_all)}/{total} ({100*len(intersect_all)/total:.2f}%)")
    print(f"  Pass@{k} union:               {len(union_all)}/{total} ({100*len(union_all)/total:.2f}%)")
    print(f"  Jointly UNRESOLVED:          {len(none_resolved)}/{total} ({100*len(none_resolved)/total:.2f}%)")

    # Per-run unique contribution
    print()
    print(f"  Unique resolves (instances solved by exactly 1 run):")
    for label, s in zip(labels, sets):
        others = set.union(*(other for other in sets if other is not s)) if k > 1 else set()
        unique = s - others
        print(f"    {label:<48} {len(unique):>4}")

    # Pairwise overlap matrix
    print()
    print(f"  Pairwise |intersection| matrix:")
    header = " " * 18 + "  ".join(f"{lbl[:14]:>14}" for lbl in labels)
    print(f"    {header}")
    for i, lbl_i in enumerate(labels):
        row = [f"{lbl_i[:16]:<16}"]
        for j, _ in enumerate(labels):
            if i == j:
                row.append(f"{'-':>14}")
            else:
                row.append(f"{len(sets[i] & sets[j]):>14}")
        print("    " + "  ".join(row))

    # k-way bucket counts (resolved by exactly m runs, for m=0..k)
    print()
    print(f"  Distribution by # of runs that resolved each instance:")
    bucket = [0] * (k + 1)
    for inst in submitted:
        m = sum(1 for s in sets if inst in s)
        bucket[m] += 1
    for m in range(k + 1):
        bar = "█" * int(60 * bucket[m] / total) if total else ""
        print(f"    {m}/{k} runs:  {bucket[m]:>4}  {bar}")

if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python3 parse_results.py <report.json> [report2.json] ... [reportN.json]")
        sys.exit(1)

    paths = sys.argv[1:]
    reports = [parse_report(p) for p in paths]
    labels = [short_label(p) for p in paths]

    for r, lbl in zip(reports, labels):
        print_single(r, lbl)

    if len(reports) == 2:
        print_pairwise(reports[0], reports[1], labels[0], labels[1])
    elif len(reports) >= 3:
        # Print all pairwise overlaps briefly
        for (i, ri), (j, rj) in combinations(enumerate(reports), 2):
            print_pairwise(ri, rj, labels[i], labels[j])
        print_passk(reports, labels)
