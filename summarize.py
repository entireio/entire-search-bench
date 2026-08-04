#!/usr/bin/env python3
"""Summarize a benchmark run: per-task and per-arm tables from results.jsonl.

Regrades every run's predicted files against the current golds in tasks.jsonl
(so gold fixes apply retroactively to raw results), then prints per-task
success counts with median wall/cost, and per-arm aggregates.

Usage:
  python3 summarize.py                          # latest results dir
  python3 summarize.py results/<ts>/results.jsonl
"""

import json
import statistics
import sys
from pathlib import Path

from run import score

HERE = Path(__file__).parent


def main():
    if len(sys.argv) > 1:
        results_path = Path(sys.argv[1])
    else:
        runs = sorted((HERE / "results").glob("*/results.jsonl"))
        if not runs:
            sys.exit("no results found")
        results_path = runs[-1]

    golds = {t["id"]: t["gold"] for t in
             (json.loads(l) for l in (HERE / "tasks.jsonl").read_text().splitlines() if l.strip())}
    rows = [json.loads(l) for l in results_path.read_text().splitlines() if l.strip()]
    ok = [r for r in rows if "error" not in r]
    for r in ok:
        r["score"] = score(golds[r["task"]], r["files"])

    arms = sorted({r["arm"] for r in rows})
    med = lambda rs, k: statistics.median(r[k] or 0 for r in rs)

    print(f"{results_path}\n")
    print(f"{'task':22s}" + "".join(f"{arm:>24s}" for arm in arms))
    for t in sorted({r["task"] for r in rows}):
        line = f"{t:22s}"
        for arm in arms:
            rs = [r for r in ok if r["task"] == t and r["arm"] == arm]
            if not rs:
                line += f"{'-':>24s}"
                continue
            cell = f"{sum(r['score'] == 1 for r in rs)}/{len(rs)} {med(rs, 'wall_s'):4.0f}s ${med(rs, 'cost_usd'):.2f}"
            line += f"{cell:>24s}"
        print(line)

    print()
    for arm in arms:
        rs = [r for r in ok if r["arm"] == arm]
        errs = sum(1 for r in rows if r["arm"] == arm and "error" in r)
        if not rs:
            print(f"{arm}: all {errs} runs errored")
            continue
        tokens = [(r["in_tokens"] or 0) + (r["cache_read_tokens"] or 0) + (r["out_tokens"] or 0) for r in rs]
        mean = lambda k: sum(r[k] or 0 for r in rs) / len(rs)
        print(f"{arm}: score={sum(r['score'] == 1 for r in rs)}/{len(rs)} err={errs}  "
              f"wall p50={med(rs, 'wall_s'):.0f}s mean={mean('wall_s'):.0f}s  "
              f"cost p50=${med(rs, 'cost_usd'):.2f} mean=${mean('cost_usd'):.2f}  "
              f"tokens p50={statistics.median(tokens):.0f} mean={sum(tokens) / len(tokens):.0f}")


if __name__ == "__main__":
    main()
