# SQL performance

## Query

Session-specific runs ordered by `(created_at DESC, id DESC)` with limit 20; review-required note queue with the same cursor ordering.

## Problem

Without a matching index PostgreSQL may scan/filter many rows and sort before limiting. OFFSET grows with page depth. Keyset pagination instead applies `(created_at,id) < (:timestamp,:id)`.

## Optimization / Index

`ix_runs_session_cursor(session_id, created_at, id)` supports session filtering and reverse index scan. `ix_notes_review_cursor(created_at,id) WHERE status='REVIEW_REQUIRED'` supports a compact review queue. Evidence result loading uses batched joins, not per-statement ORM lazy access.

## Execution plan / Before / After

Benchmark script and measured evidence will be added after the first working checkpoint. No performance improvement number is claimed at this checkpoint.
