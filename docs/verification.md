# Verification record

Final local verification: 2026-09-09. Python 3.12.14, PostgreSQL 16.4
(native Windows), Redis 8.10.1 (community Windows/Cygwin build).
Compose targets PostgreSQL 16 and Redis 7.4 on Linux.
All test/demo data are synthetic.

| Check | Status | Evidence |
|---|---|---|
| Existing installed dependencies | VERIFIED | `python -m pip check`: no broken requirements |
| Fresh dependency installation | BLOCKED BY ENVIRONMENT | `pip install -e '.[dev]'` attempted; outbound package access denied (WinError 10013), setuptools build dependency unavailable |
| Ruff lint | VERIFIED | `ruff check app tests scripts` |
| Ruff formatting | VERIFIED | `ruff format --check app tests scripts` |
| Strict mypy | VERIFIED | `mypy app`: no issues in 13 source files |
| Unit + integration regression | VERIFIED | **40 passed in 15.70s**, PostgreSQL/Redis, no skipped tests |
| Alembic migration | VERIFIED | `python -m alembic upgrade head` on application and dedicated test DB; `alembic check` found no schema drift |
| API + worker startup | VERIFIED | Independent native processes, readiness HTTP 200 |
| REST demo | VERIFIED | `python -m scripts.demo`: scenarios A-D, approval, immutable replay and comparison |
| WebSocket | VERIFIED | Live Uvicorn socket consumes Redis progress from worker in integration test |
| Browser interaction | VERIFIED | Headless Edge: correction, replay, comparison, evidence highlight, approval, unsupported rejection; no JavaScript errors |
| UI layout | VERIFIED | Desktop screenshot inspected; mobile screenshot captured |
| Metrics | VERIFIED | API `/metrics` and worker `:9000/metrics`; inference histogram exposed |
| SQL execution plans | VERIFIED | 200,000 synthetic rows; five before/after samples, raw JSON saved in the working tree |
| End-to-end bounded load test | VERIFIED | 6 workflows, concurrency 2, 36 HTTP requests and 6 WebSockets; 6 successes / 0 errors in 1.323 s. Raw report: `docs/load-test-results.json` |
| Compose YAML structure | VERIFIED | YAML parsed; services and inherited database environment checked |
| Docker build / compose config / container startup | BLOCKED BY ENVIRONMENT | Docker and Podman executables unavailable; YAML parsing is not Docker validation |
| Hosted GitHub Actions | NOT VERIFIED | Workflow configured, no passing badge or hosted-run claim |
| AWS deployment | NOT VERIFIED | Architecture design only; no resources applied |
| New checkpoint commits | BLOCKED BY ENVIRONMENT | `.git` is read-only; `git add` and `git commit` failed with index.lock permission denied |
| Remote push | NOT VERIFIED | Not attempted because checkpoint creation is blocked |

## Regression coverage

16 deterministic unit cases and 24 integration/contract cases cover normal inference,
strict output-schema failure, unsupported claims, contradiction, correction/stale
references, post-approval invalidation, concurrent idempotency, provider timeout,
permanent/transient failure and bounded retry, human approval/rejection/audit,
immutable replay/comparison, redaction before provider invocation, valid audio,
invalid STT output rollback, PostgreSQL uniqueness and transaction rollback,
pagination, API-key boundary, body/rate limits, pending consumer recovery,
Redis stream loss republishing, acknowledged-message cleanup and live WebSocket.

## Recovery performed

Initial regression had 15 passes and 16 setup errors because the existing native
PostgreSQL process was stopped. Its data directory was preserved. After native
startup and crash recovery, the original 31-test suite passed. Additional recovery
and boundary tests brought the suite to 34. Ruff import/format failures were fixed.
PowerShell Start-Process encountered duplicate PATH environment keys; Python's
hidden subprocess startup successfully launched the existing PostgreSQL binary.
No database reset or SQLite substitution was used for the application data.

## Reproduce

Follow README to start PostgreSQL/Redis and create a dedicated `mindflow_test` DB.
Then run:

```sh
python -m pytest -q
ruff check app tests scripts
ruff format --check app tests scripts
mypy app
python -m alembic upgrade head
python -m scripts.demo
python -m scripts.benchmark --rows 200000 --output reports/local/performance.json
python -m scripts.load_test --environment "local API + worker + PostgreSQL/Redis" --jobs 6 --concurrency 2 --output docs/load-test-results.json
docker compose config --quiet
docker compose build
```

The last two commands remain to be executed on a Docker-enabled machine. Test DB
names must end in `_test`; test fixtures truncate that DB's domain tables and
clear only the `mindflow-test:*` Redis namespace. Native development uses an ignored
runtime directory and loopback-only infrastructure; this is not deployment setup.

## Git handoff

Existing checkpoint: `00ca56a feat: deliver runnable evidence inference and human review backend`.
Changes remain in the working tree because this execution profile prohibits `.git`
writes. The intended checkpoints are worker recovery/ingestion hardening, typed
API contracts, and measured performance/final documentation. Inspect `git diff`
and commit these changes from a writable repository session before pushing to the
existing origin. Never stage `.runtime`, `.env`, credentials or real input data.
