# SQL performance

## Query

Session-specific runs ordered by `(created_at DESC, id DESC)` with limit 20; review-required note queue with the same cursor ordering.

## Problem

Without a matching index PostgreSQL may scan/filter many rows and sort before limiting. OFFSET grows with page depth. Keyset pagination instead applies `(created_at,id) < (:timestamp,:id)`.

## Optimization / Index

`ix_runs_session_cursor(session_id, created_at, id)` supports session filtering and reverse index scan. `ix_notes_review_cursor(created_at,id) WHERE status='REVIEW_REQUIRED'` supports a compact review queue. Evidence result loading uses batched joins, not per-statement ORM lazy access.

## Execution plan / Before / After

Measured on 2026-09-08 with PostgreSQL 16.4 on native Windows. Run:

```sh
python -m scripts.benchmark --rows 200000 --output reports/local/performance.json
```

The script creates a temporary synthetic projection with 200,000 rows, 1,000 session IDs,
a 512-character JSON payload and a 1% review-required fraction. It runs five
`EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON)` samples before and after adding indexes
in execution order; there is no separate cache-warming phase.
It does not alter application tables or drop production indexes. The session query
matches the run-list access pattern; the review query illustrates the note index,
not an implemented global review-list endpoint.

| Query | Before median (ms) | After median (ms) | Plan change |
|---|---:|---:|---|
| Session runs, latest 20 | 87.660 | 0.027 | Sequential scan + sort → backward index-only scan |
| Review-required, latest 20 | 91.413 | 0.017 | Sequential scan + sort → backward partial index-only scan |

Raw samples, buffer counts, SQL and complete representative plans:
[performance-results.json](performance-results.json).

These are local synthetic SQL timings, not API latency, production throughput or
LLM performance. Index creation cost is excluded. Cache state and the fixed data
distribution favor indexed reads; rerun on deployment-sized data before making
capacity decisions. PostgreSQL can choose different plans for different selectivity.

## End-to-end synthetic load

`scripts/load_test.py` exercises session creation, transcript submission, inference
queueing, PostgreSQL persistence, Redis dispatch, worker completion, WebSocket
progress, result retrieval and synthetic approval. A bounded run on 2026-09-09
used native PostgreSQL 16.4 and Redis 8.10.1 on Windows 11, with 6 workflows at
concurrency 2 and a 30-second per-job timeout. All 6 workflows succeeded (36
HTTP requests, 6 WebSockets, 0 errors) in 1.323 seconds. Measured client-side
workflow latency was p50 501.709 ms / p95 673.639 ms; successful workflow
throughput was 4.534 per second. These are one local synthetic run, with no
warm-up phase or cleanup; raw output is in [load-test-results.json](load-test-results.json).
