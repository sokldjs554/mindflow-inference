# Architecture

FastAPI modular monolith + one worker process; `app/api.py` handles transport, `services.py` transactions/review/replay, `worker.py` pipeline, `providers.py` provider contracts, `queue.py` delivery. No distributed service calls between domain modules.

```mermaid
sequenceDiagram
    Client->>API: Create inference + idempotency key
    API->>PostgreSQL: Lock session; snapshot + job + outbox + key
    API-->>Client: 202 job ID
    Worker->>PostgreSQL: Select unpublished outbox SKIP LOCKED
    Worker->>Redis: XADD jobs
    Worker->>PostgreSQL: Mark published
    Worker->>Redis: XREADGROUP / XAUTOCLAIM
    Worker->>PostgreSQL: Advisory job lock
    Worker->>Provider: Redacted transcript
    Provider-->>Worker: Structured candidate
    Worker->>PostgreSQL: Validated result + event (atomic)
    Worker->>Redis: XADD progress; XACK job
    Redis-->>API: XREAD progress
    API-->>Client: WebSocket progress
    Client->>API: Human review
    API->>PostgreSQL: Session/note locks; active evidence gate + audit
```

Publication is at least once: crash after XADD before DB commit may duplicate. DB advisory locks and unique run-per-job constraint prevent duplicate results. Worker uses a dedicated PostgreSQL connection for advisory lock lifetime, with short independent transactions for observable steps. This consumes two connections per active worker and deliberately avoids external calls inside row-lock transactions.

Original input belongs inside the database boundary. Only redacted text crosses the LLM protocol. Mock STT runs locally; real external STT would receive raw audio and needs a separately assessed boundary. Exceptions are reduced to safe codes, request access logs disabled in documented startup commands.

Redis outage leaves committed outboxes for recovery. PostgreSQL outage fails readiness; liveness remains independent. Worker retries infrastructure failures in its main loop. See pipeline for provider retry semantics.
