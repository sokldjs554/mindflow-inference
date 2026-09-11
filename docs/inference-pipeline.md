# Inference pipeline

States: QUEUED, INGESTING, optional TRANSCRIBING, REDACTING, GENERATING, VALIDATING, REVIEW_REQUIRED. Human approve/reject transitions job to COMPLETED, note to APPROVED/REJECTED. Inference completion timestamp records generation end, while note reviewed_at records human action.

Provider timeout defaults to 5 seconds. Maximum 3 attempts, backoff 1s/2s by default. Schema failure is permanent and creates no result. Provider exceptions are persisted only as safe codes. Retry count lives in DB, so restart cannot reset it. Each successful job has one run; failed attempts retain job events, not unvalidated raw output.

DB outbox publishing and Redis XACK ordering provide at-least-once processing. Pending entries are recovered by XAUTOCLAIM after 2 seconds. PostgreSQL session advisory locks exclude simultaneous redelivery while a provider call is in flight. Advisory locks are released on disconnect. Run uniqueness is an additional invariant.

The dispatcher republishes nonterminal jobs after 30 seconds, so loss of the Redis
job stream does not strand already-published DB outboxes. Recovery advances the
XAUTOCLAIM cursor across pending entries. Successfully acknowledged messages are
deleted from the single-group job stream; pending jobs are never trimmed. Invalid
job envelopes are acknowledged and deleted. Redis progress history lost after
publication is not rebuilt; the reconnect snapshot remains authoritative.

STT output passes the transcript schema and session constraints before persistence.
An intervening transcript submission fails audio ingestion with INPUT_CONFLICT;
the transaction preserves existing utterances. Redaction and evidence validation
providers are injected through Protocol contracts, using deterministic defaults.

Redis progress events have event UUID and stream cursor. Outbox publication can duplicate an event. Durable state is read on WebSocket connection; cursor capture happens before the DB snapshot to avoid a subscription gap. Consumers should deduplicate event IDs. Historical events can precede the initial snapshot on delayed outbox publication; clients must use snapshot/current state as authoritative.

Mock STT returns a fixed synthetic fixture for a valid uploaded WAV. That transcript is persisted in the job snapshot before generation. Replay therefore does not retranscribe audio. Mock LLM copies client utterances; note-v2 excludes explicitly superseded utterances. Test-injected providers exercise schema failure, timeout, transient failure and permanent failure through the same worker.

Prompt version catalog is insert-only in application code. No public mutation endpoints. Provenance includes template hash and provider mode. Successful-attempt latency excludes prior backoff; started_at and completed_at span the job's attempts. Failed-run comparison is not supported because invalid outputs are not retained as notes.
