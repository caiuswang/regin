"""Measure how much of a repo's history is rework: fixes to code we just shipped.

Read-only over `git log`; no regin DB, no capture required. This is the
coarse repo-level gauge beside the per-feature one (*Feature pipeline* in
ARCHITECTURE.md): it shows the shape of rework in aggregate, but names no
feature, bug, or missing check.

    .venv/bin/python scripts/rework_probe.py --since "120 days ago"
"""

from __future__ import annotations

import argparse
import collections
import re
import statistics
import subprocess
from dataclasses import dataclass, field

FIX_SUBJECT = re.compile(r"^(fix|bugfix|hotfix|revert)\b", re.IGNORECASE)
SCOPE = re.compile(r"^\w+\(([^)]+)\)")
AGE_BUCKETS = (("<1d", 1.0), ("1-3d", 3.0), ("3-7d", 7.0), ("7-30d", 30.0), (">30d", float("inf")))


@dataclass
class Commit:
    sha: str
    ts: int
    subject: str
    files: list[tuple[str, int, int]] = field(default_factory=list)

    @property
    def is_fix(self) -> bool:
        return bool(FIX_SUBJECT.match(self.subject))

    @property
    def scope(self) -> str:
        m = SCOPE.match(self.subject)
        return m.group(1) if m else "(unscoped)"


def read_log(since: str, path_filter: list[str]) -> list[Commit]:
    cmd = ["git", "log", "--no-merges", f"--since={since}", "--numstat", "--format=@@%H|%ct|%s"]
    if path_filter:
        cmd += ["--"] + path_filter
    raw = subprocess.run(cmd, capture_output=True, text=True, check=True).stdout
    commits: list[Commit] = []
    for line in raw.splitlines():
        if line.startswith("@@"):
            sha, ts, subject = line[2:].split("|", 2)
            commits.append(Commit(sha=sha, ts=int(ts), subject=subject))
        elif line.strip() and commits:
            parts = line.split("\t")
            if len(parts) == 3 and parts[0].isdigit() and parts[1].isdigit():
                commits[-1].files.append((parts[2], int(parts[0]), int(parts[1])))
    commits.reverse()
    return commits


def bucket_for(age_days: float) -> str:
    for label, ceiling in AGE_BUCKETS:
        if age_days < ceiling:
            return label
    return AGE_BUCKETS[-1][0]


def churn_by_age(commits: list[Commit]) -> tuple[collections.Counter, int]:
    """Lines replaced, bucketed by how old the code they replaced was."""
    last_touch: dict[str, int] = {}
    replaced: collections.Counter = collections.Counter()
    for c in commits:
        for path, _added, deleted in c.files:
            if deleted and path in last_touch:
                replaced[bucket_for((c.ts - last_touch[path]) / 86400)] += deleted
            last_touch[path] = c.ts
    return replaced, sum(replaced.values())


def fix_latencies(commits: list[Commit]) -> list[float]:
    """Days from a file's previous commit to the fix commit that touches it again."""
    last_touch: dict[str, int] = {}
    gaps: list[float] = []
    for c in commits:
        if c.is_fix:
            ages = [(c.ts - last_touch[p]) / 86400 for p, _a, _d in c.files if p in last_touch]
            if ages:
                gaps.append(min(ages))
        for path, _a, _d in c.files:
            last_touch[path] = c.ts
    gaps.sort()
    return gaps


def report(commits: list[Commit]) -> None:
    fixes = [c for c in commits if c.is_fix]
    pct = 100 * len(fixes) / max(len(commits), 1)
    print(f"commits {len(commits)}   fix-commits {len(fixes)} ({pct:.1f}%)")

    replaced, total = churn_by_age(commits)
    print("\nlines replaced, by age of the code they replaced:")
    for label, _ in AGE_BUCKETS:
        n = replaced[label]
        print(f"  {label:<8}{n:>8}  {100 * n / max(total, 1):>5.1f}%")

    gaps = fix_latencies(commits)
    if gaps:
        print(f"\nfeature -> fix latency over {len(gaps)} fix-commits:")
        print(f"  median {statistics.median(gaps):.2f}d   p25 {gaps[len(gaps) // 4]:.2f}d"
              f"   p75 {gaps[3 * len(gaps) // 4]:.2f}d")
        for label, thr in (("6h", 0.25), ("1d", 1.0), ("3d", 3.0), ("7d", 7.0)):
            n = sum(1 for g in gaps if g < thr)
            print(f"  within {label:<3} {n:>4}/{len(gaps)}  ({100 * n / len(gaps):.0f}%)")

    by_scope = collections.Counter(c.scope for c in fixes)
    print("\nfix-commits by scope:")
    for scope, n in by_scope.most_common(12):
        print(f"  {scope:<16}{n:>4}")


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--since", default="120 days ago", help="git --since expression")
    ap.add_argument("paths", nargs="*", help="restrict to these pathspecs")
    args = ap.parse_args()
    commits = read_log(args.since, args.paths)
    if not commits:
        print(f"no commits since {args.since}")
        return
    report(commits)


if __name__ == "__main__":
    main()
