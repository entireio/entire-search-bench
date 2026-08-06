# Agent code-localization: `entire search` vs clone+grep

Headless Claude Code agent answers "which file implements X?" and must cite a verified file. Score = any predicted file matches a gold file. 10 tasks × 10 runs × 2 arms per experiment.

## 1. Cross-repo (org-wide, empty workspace) — Sonnet 4.6, 2026-08-03

Agent starts in an empty dir; grep arm must discover + clone repos, search arm has `entire checkpoint search --all-repos`.

| arm | score | wall p50 / mean | cost mean | tokens mean |
|---|---|---|---|---|
| grep | 97/99 (+1 timeout) | 55s / 94s | $0.24 | 363K |
| search | **100/100** | **36s / 58s** | **$0.15** | **142K** |

Gap scales with scope: `repos-resolver-swr` 204s/$0.48 → 23s/$0.07 (9× faster, 7× cheaper); `bff-merge` 354s/$0.90 → 120s/$0.28. All grep failures were on cross-repo tasks.

## 2. Single repo, keyword questions — Opus 4.6, 2026-08-05

Warm checkout of entirehq/entire-search; questions contain greppable identifiers.

| arm | score | wall mean | cost mean | turns p50 |
|---|---|---|---|---|
| grep | 100/100 | 31s | $0.19 | 6 |
| search | 100/100 | 29s | $0.20 | 6 |

Tie. Both arms at the floor: locate (1 turn either way) → read → answer.

## 3. Single repo, vocabulary-mismatch questions — Opus 4.6, 2026-08-05

Same checkout; questions worded so no distinctive identifier from the gold file appears in the question (the case expected to favor semantic search).

| arm | score* | wall mean | cost mean | turns mean |
|---|---|---|---|---|
| grep | 90/100 | 35s | $0.20 | 7.0 |
| search | 91/100 | 39s | $0.23 | 7.3 |

*Misses shared: 17/20 misses on one task where both arms converged on the same defensible alternate file (any-of regrading → ~99/100 both). Hardest task cost ~15 turns in both arms.

Still a tie. The expected semantic edge doesn't materialize in-repo because: (1) the file tree is already a semantic index (`ls` returns `reranker.go`, `strip.go` — names answer the question); (2) a frontier model closes vocabulary gaps itself by grepping synonyms; (3) search result pages are token-heavy JSON, offsetting saved turns (97K vs 79K input tokens).

## Summary

`entire search` is cost-neutral on repo-local questions — same accuracy, ±10% wall/cost — regardless of how the question is phrased. Its advantage is scope: it eliminates the "which repo, and does prior work exist?" phase, which is worth up to 9× wall / 7× cost org-wide and is exactly the phase that vanishes inside a warm checkout. One line: **as fast as grep locally, up to 9× faster everywhere else.**

Data: `results/20260803-195916` (cross-repo), `results/20260805-180131` (keyword), `results/20260805-194531` (vocab-mismatch). Tasks: `tasks.jsonl`, `tasks_singlerepo.jsonl`, `tasks_singlerepo_semantic.jsonl`.
