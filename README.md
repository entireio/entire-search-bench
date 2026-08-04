# entire-search-bench

Can an AI coding agent answer *"where does this logic live in our org's code?"*
faster and cheaper with [`entire checkpoint search`](https://entire.io) than by
cloning repos and grepping?

This is the harness, tasks, and raw results behind the benchmark in our launch
post. It is **not** a general-purpose public benchmark: the tasks target our
own private repos, because that's the point — `entire` searches the agent
sessions, checkpoints, and commits of *your* org, which no public corpus has.
We publish it so the methodology and grading are inspectable and so you can
rerun the same experiment against your own org.

## Design

Each run launches a headless Claude Code agent (`claude -p`, Sonnet 4.6) in a
fresh empty workspace with a 600s timeout and asks one localization question.
Two arms:

| arm | tools |
|---|---|
| `grep` | may shallow-clone any org repo and search with grep/ripgrep; the `entire` CLI is blocked (`--disallowedTools "Bash(entire:*)"`) |
| `search` | same tools **plus** the `entire` CLI, with a hint to prefer `entire checkpoint search --all-repos` |

The agent must end its reply with `{"files": ["owner/repo:path"]}`. Both arms
are required to verify every named file exists in this session (no answering
from prior knowledge). A run scores 1.0 if any predicted file matches any of
the task's acceptable gold files.

10 tasks (see `tasks.jsonl`) span three repos across two services and three
languages (Go, TypeScript, Rust). Some tasks list multiple acceptable golds:
during grading we audited every miss and found agents converging on real
duplicate implementations (a legacy TypeScript twin of the Go pipeline) or on
a file that answered the question better than our original gold. Every
alternative was verified to contain the described logic before being accepted;
zero hallucinated paths occurred across 200 runs.

## Results (10 tasks × 10 runs × 2 arms, Sonnet 4.6, 2026-08-03)

```
task                                      grep                  search
bff-merge                      8/9  354s $0.90       10/10  120s $0.28
bff-telemetry                10/10   60s $0.16       10/10   25s $0.10
derive-start                 10/10   38s $0.14       10/10   23s $0.08
embed-rate-limiter           10/10   52s $0.14       10/10   28s $0.11
peregrine-lifecycle          10/10   45s $0.13       10/10   53s $0.13
peregrine-ranking            10/10   46s $0.13       10/10   42s $0.11
repos-resolver-swr           10/10  204s $0.48       10/10   23s $0.07
rerank-floor                  9/10   57s $0.13       10/10   35s $0.12
tier-assignment              10/10   63s $0.17       10/10   32s $0.09
transcript-strip             10/10   40s $0.13       10/10   48s $0.12

grep:   score=97/99 err=1  wall p50=55s mean=94s  cost p50=$0.14 mean=$0.24  tokens p50=139646 mean=363086
search: score=100/100 err=0 wall p50=36s mean=58s  cost p50=$0.10 mean=$0.15  tokens p50=63916  mean=141506
```

- **Accuracy**: search 100/100; grep 97/99 plus one 600s timeout.
- **Speed**: 1.5× faster at the median, 1.6× at the mean.
- **Tokens/cost**: 2.6× fewer tokens, 1.6× cheaper on average.
- **The gap scales with scope.** On single-repo questions the arms tie. On
  cross-repo questions grep means cloning half the org: `repos-resolver-swr`
  went from 204s/$0.48 (30–45 tool turns) to 23s/$0.07 (2–3 turns) — 9× faster,
  7× cheaper. Grep's only failures (a timeout and a wrong answer after 60
  turns) were on exactly these tasks.

Raw per-run data: `results/20260803-195916/results.jsonl`.

## Caveats

- Tasks were authored by us over our own repos: task-selection bias applies.
- The search arm keeps clone/grep available; this measures "agent with search
  added", not "search alone".
- Single model (Sonnet 4.6), n=10 per cell, wall clock includes API variance.
- Grading is file-level, any-of across verified duplicate implementations.

## Running it against your own org

Requirements: `claude` CLI, `git`/`gh` access to your org, and the `entire`
CLI logged in. Edit `ANCHOR_REMOTE` in `run.py` to any repo in your org and
write your own `tasks.jsonl`.

```
python3 run.py --selftest                 # harness sanity check, no API calls
python3 run.py --arms search --tasks my-task   # smoke-test one task
python3 run.py --runs 10 --workers 5      # full run (spends real API dollars)
python3 summarize.py                      # tables from the latest results
```
