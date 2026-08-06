#!/usr/bin/env python3
"""Agent search benchmark: clone+grep vs `entire checkpoint search`.

For each task in tasks.jsonl, runs a headless Claude Code agent in a fresh
workspace under one of two arms:

  grep    - may clone entirehq repos and grep them; `entire` CLI is blocked
  search  - additionally has the `entire` CLI and is told to prefer it
  skill   - uses the packaged `entire:search` plugin skill (entireio/skills),
            i.e. what interactive users get via /entire:search

The agent must end its reply with a JSON block {"files": ["owner/repo:path"]}.
A run scores 1.0 if any predicted file matches any of the task's acceptable
gold files (some logic is legitimately implemented in more than one place —
see tasks.jsonl). Wall-clock, tokens, and cost come from the claude -p JSON
envelope.

Usage:
  python3 run.py                        # all tasks, both arms
  python3 run.py --arms search --tasks tier-assignment
  python3 run.py --selftest
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import tempfile
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

# claude refuses to launch inside a claude session; benchmark children are isolated workspaces.
CHILD_ENV = {k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}

HERE = Path(__file__).parent
# The `entire` CLI anchors auth/org detection on the cwd's git origin, so each
# workspace gets a bare `git init` plus any org repo as its origin remote.
ANCHOR_REMOTE = "git@github.com:entirehq/entire-search.git"

COMMON_PROMPT = """You are being benchmarked on code localization across an organization's repos.

Question: {question}

The relevant code lives in the GitHub org `entirehq`. You have git/gh access.
Identify the specific file(s) that answer the question. Be minimal: only the
file(s) where the described logic actually lives.

Rules:
- Do NOT answer from prior knowledge. Every file you name must be backed by
  evidence you gathered in this session.
- Verify each named file actually exists (read it, list it, or see its exact
  path in tool output). Never guess a plausible-sounding path.

End your reply with a fenced JSON block exactly like:
```json
{{"files": ["owner/repo:path/from/repo/root"]}}
```
"""

ARM_HINTS = {
    "grep": "You may shallow-clone repos into the current directory and search them with grep/ripgrep. The `entire` CLI is NOT available.",
    "search": (
        "You have the `entire` CLI (already logged in). Prefer it over cloning:\n"
        '  entire checkpoint search "<query>" --all-repos --json --limit 10\n'
        "It does semantic + keyword search over sessions, checkpoints, and commits across all repos; "
        "results include commit info and filesTouched. Inline filters like repo:owner/name work inside the query. "
        "Cloning is allowed but should rarely be needed."
    ),
    "skill": (
        "You have the `entire` Claude Code plugin installed (CLI already logged in). "
        "Start by invoking its `entire:search` skill and follow the skill's guidance to answer. "
        "Cloning is allowed but should rarely be needed."
    ),
}

ARM_TOOL_FLAGS = {
    "grep": ["--allowedTools", "Bash,Read,Grep,Glob", "--disallowedTools", "Bash(entire:*)"],
    "search": ["--allowedTools", "Bash,Read,Grep,Glob"],
    # skill arm measures the packaged plugin path (entireio/skills); requires
    # `claude plugin install entire@entire-skills` on the host.
    "skill": ["--allowedTools", "Bash,Read,Grep,Glob,Skill"],
}


def extract_files(text):
    """Pull {"files": [...]} out of the last fenced json block (or bare JSON)."""
    blocks = re.findall(r"```(?:json)?\s*(\{.*?\})\s*```", text, re.DOTALL)
    candidates = blocks or re.findall(r"\{[^{}]*\"files\"[^{}]*\}", text, re.DOTALL)
    for raw in reversed(candidates):
        try:
            obj = json.loads(raw)
            if isinstance(obj.get("files"), list):
                return [str(f) for f in obj["files"]]
        except (json.JSONDecodeError, AttributeError):
            continue
    return []


def norm(ref):
    """'owner/repo:path' -> ('repo', 'path') lowercased, leading ./ stripped."""
    repo, _, path = ref.partition(":")
    repo = repo.rstrip("/").split("/")[-1].lower()
    return repo, path.strip().lstrip("./").lower()


def score(gold, predicted):
    """1.0 if any predicted file matches any acceptable gold file, else 0.0.

    gold is a list of acceptable alternatives (repo name + path suffix match);
    each task has one logical answer that may exist in multiple places.
    """
    preds = [norm(p) for p in predicted]
    for g in gold:
        grepo, gpath = norm(g)
        for prepo, ppath in preds:
            if grepo == prepo and ppath and (ppath.endswith(gpath) or gpath.endswith(ppath)):
                return 1.0
    return 0.0


def run_one(task, arm, model, timeout):
    ws = Path(tempfile.mkdtemp(prefix=f"esb-{task['id']}-{arm}-"))
    try:
        subprocess.run(["git", "init", "-q"], cwd=ws, check=True)
        subprocess.run(["git", "remote", "add", "origin", ANCHOR_REMOTE], cwd=ws, check=True)
        prompt = COMMON_PROMPT.format(question=task["question"]) + "\n" + ARM_HINTS[arm]
        cmd = ["claude", "-p", prompt, "--output-format", "json", "--model", model,
               *ARM_TOOL_FLAGS[arm]]
        t0 = time.monotonic()
        try:
            proc = subprocess.run(cmd, cwd=ws, capture_output=True, text=True, timeout=timeout, env=CHILD_ENV)
        except subprocess.TimeoutExpired:
            return {"task": task["id"], "arm": arm, "error": "timeout", "wall_s": timeout}
        wall = time.monotonic() - t0
        try:
            env = json.loads(proc.stdout)
        except json.JSONDecodeError:
            return {"task": task["id"], "arm": arm, "error": f"bad envelope (rc={proc.returncode}): {proc.stdout[:200]} {proc.stderr[:200]}", "wall_s": round(wall, 1)}
        answer = env.get("result", "") or ""
        files = extract_files(answer)
        usage = env.get("usage", {})
        return {
            "task": task["id"], "arm": arm,
            "score": score(task["gold"], files),
            "files": files,
            "wall_s": round(wall, 1),
            "agent_ms": env.get("duration_ms"),
            "turns": env.get("num_turns"),
            "cost_usd": env.get("total_cost_usd"),
            "in_tokens": usage.get("input_tokens"),
            "cache_read_tokens": usage.get("cache_read_input_tokens"),
            "out_tokens": usage.get("output_tokens"),
        }
    finally:
        shutil.rmtree(ws, ignore_errors=True)


def selftest():
    assert extract_files('blah\n```json\n{"files": ["a/b:c/d.go"]}\n```') == ["a/b:c/d.go"]
    assert extract_files("no json here") == []
    assert score(["entirehq/entire-search:internal/searcher/turbopuffer.go"],
                 ["entire-search:internal/searcher/turbopuffer.go"]) == 1.0
    assert score(["o/r:a/b.go"], ["o/r:x/y.go"]) == 0.0
    assert score(["o/r:a/b.go", "o/r2:c.ts"], ["github.com/o/r2:c.ts"]) == 1.0
    assert score(["o/r:a/b.go"], []) == 0.0
    print("selftest OK")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--arms", default="grep,search")
    ap.add_argument("--tasks", default="", help="comma-separated task ids (default all)")
    ap.add_argument("--model", default="claude-sonnet-4-6")
    ap.add_argument("--runs", type=int, default=1)
    ap.add_argument("--workers", type=int, default=5)
    ap.add_argument("--timeout", type=int, default=600)
    ap.add_argument("--selftest", action="store_true")
    args = ap.parse_args()
    if args.selftest:
        selftest()
        return

    tasks = [json.loads(l) for l in (HERE / "tasks.jsonl").read_text().splitlines() if l.strip()]
    if args.tasks:
        keep = set(args.tasks.split(","))
        tasks = [t for t in tasks if t["id"] in keep]
    arms = args.arms.split(",")

    out = HERE / "results" / time.strftime("%Y%m%d-%H%M%S")
    out.mkdir(parents=True)
    jobs = [(run, task, arm) for run in range(args.runs) for task in tasks for arm in arms]
    results = []
    lock = threading.Lock()

    def worker(job):
        run, task, arm = job
        r = run_one(task, arm, args.model, args.timeout)
        r["run"] = run
        with lock:
            results.append(r)
            print(f"[{len(results)}/{len(jobs)}] {json.dumps({k: r[k] for k in r if k != 'files'})}", flush=True)
            (out / "results.jsonl").open("a").write(json.dumps(r) + "\n")

    with ThreadPoolExecutor(max_workers=args.workers) as pool:
        list(pool.map(worker, jobs))

    print(f"\nresults: {out}/results.jsonl\n")
    for arm in arms:
        rs = [r for r in results if r["arm"] == arm and "error" not in r]
        errs = len([r for r in results if r["arm"] == arm and "error" in r])
        if not rs:
            print(f"{arm:7s} all {errs} runs errored")
            continue
        mean = lambda k: sum(r[k] or 0 for r in rs) / len(rs)
        print(f"{arm:7s} n={len(rs)} err={errs} score={mean('score'):.2f} "
              f"wall_s={mean('wall_s'):.0f} cost=${mean('cost_usd'):.3f} "
              f"tokens_in={mean('in_tokens'):.0f}+cache{mean('cache_read_tokens'):.0f} out={mean('out_tokens'):.0f}")


if __name__ == "__main__":
    main()
