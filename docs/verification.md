# Verification record

Initial working checkpoint: Python 3.12.14, PostgreSQL 16.4 (native Windows), Redis 8.10.1 (community Windows/Cygwin build). Production Compose targets PostgreSQL 16 and Redis 7.4 on Linux.

- VERIFIED: dependency installation; Alembic upgrade; PostgreSQL/Redis connectivity.
- VERIFIED: initial `pytest -q`: **31 passed in 13.61s**, including live WebSocket and real worker stream events.
- VERIFIED: initial `ruff check app`; strict `mypy app` (13 modules).
- VERIFIED: independent API and worker processes; `python -m scripts.demo` completed A-D,
  human approval and immutable-input replay/comparison through live HTTP.
- VERIFIED: regression after Windows loopback-address fix: 31 tests passed; lint/type checks passed.
- NOT VERIFIED at this checkpoint: Docker build/Compose execution (Docker not installed); hosted CI; AWS deployment.

This file will be updated with the final regression and smoke-test results. Tests use synthetic data only. Native database used loopback-only trust authentication in an ignored runtime directory; this is local test infrastructure, not a deployment configuration.
