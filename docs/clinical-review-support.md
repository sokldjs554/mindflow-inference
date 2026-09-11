# Clinical Review Support verification

Verified locally on 2026-09-11 with synthetic data only.

## Changes

- UI: `app/static/index.html`, `app/static/app.js`. Existing CSS classes reused.
- Backend: new `app/clinical.py`; extended `app/schemas.py`, `app/worker.py`,
  `app/services.py`. Existing API routes, PostgreSQL tables, Redis, outbox,
  WebSocket events, note validation and review constraints retained.
- Tests: `tests/test_clinical.py` adds 16 cases to the existing 40.
- README documents scope, rules, review semantics and legacy/replay behavior.

The clinical snapshot lives in `InferenceRun.provider_metadata.clinical_support`
inside the existing JSONB column. It is committed with the note in the worker
transaction. Read responses add run ID/time, current correction-aware validation
and the current shared review's reviewer/time. Historical item validation remains
unchanged. Old runs without a clinical snapshot return null. No migration needed.

Rule grounding uses the existing exact-source validator plus matching the item
identity, category, text and evidence against allowed deterministic proposals.
Arbitrary candidates, including claims citing a real but irrelevant reference,
are UNSUPPORTED. The existing shared review endpoint blocks approval when any
note or clinical item lacks current support. Rejections remain available. Existing
ReviewAction/AuditEvent records describe the shared decision. Corrections reset
approval and mark clinical links stale. Replay retains the immutable original
input and records the rules used by the new execution.

## Results

| Check | Result |
|---|---|
| `python -m pytest -q -p no:cacheprovider` | 56 passed in 24.89 seconds |
| `ruff check app tests scripts` | Passed |
| `ruff format --check app tests scripts` | 25 files already formatted |
| `mypy app` | Passed, 14 source files |
| `node --check app/static/app.js` | Passed |
| Headless Microsoft Edge / Playwright | Passed |

The initial pytest attempt failed because PostgreSQL was stopped. Starting the
existing local PostgreSQL executable resolved it. The final suite used the
dedicated `mindflow_test` database and existing test Redis namespace. Cache was
disabled because the preexisting pytest cache was not writable.

Browser verification used a separate `mindflow_clinical_demo` database, API port
8011, worker metrics port 9011, and `mindflow-clinical-browser` stream prefix,
because preexisting API/worker processes on 8000/9000 still ran older code.
Scenario E verified generated pattern candidates, missing information/questions,
source highlighting, shared approval, reviewer/audit display and review-required
replay. Scenarios B/C verified correction, replay/comparison, evidence inspection,
unsupported approval blocking and rejection. Desktop (1440px) and mobile (390px)
screenshots were inspected; no browser JavaScript errors or mobile horizontal
overflow were detected. Local screenshots and the browser harness are in
`.runtime/clinical-*.png` and `.runtime/clinical_browser_check.py`.

## Deliberate limits

This implements review support, not diagnostic inference. Condition candidates
are co-occurring expression patterns, not named diseases or differential
diagnoses. No diagnostic criteria, calibrated confidence, severity scores,
suicide assessment, emergency triage, medication or prescription rules exist.
Only a narrow Korean phrase catalog is recognized. Negation, history, third-party
speech and questions are conservatively excluded by literal patterns, without
claiming general language understanding. Missing information means a rule did
not find it, not proof it was never supplied. Absence of signals is not safety.

SUPPORTED describes source/rule grounding, never clinical validity. Items remain
clinical review candidates even when the shared review is APPROVED; approval
records a human review and does not finalize a diagnosis or treatment. The UI
states this alongside the panel and review controls. Clinical final/prescription
states are not accepted by the typed schemas. Reviewer aliases are not verified
clinician identities; production clinician authentication was not added.

The existing deterministic mock LLM/STT and synthetic-data scope are unchanged.

## Presentation semantics refinement (2026-09-11)

The UI now separates item meaning from internal source/rule validation:

| Kind | Primary badge | Source buttons |
|---|---|---|
| Observed signals | EVIDENCE_MATCHED | Evidence, only existing snapshot references |
| Condition candidates | RULE_DERIVED_CANDIDATE | Rule input, not proof of a condition |
| Risk signals | RULE_MATCH | Rule input, not a clinical risk assessment |
| Missing information | NOT_OBSERVED | None |
| Follow-up questions | SUGGESTED | None |
| Next assessment | RECOMMENDED_FOR_REVIEW | None |
| AI recommendation | REVIEW_CANDIDATE | None |

Candidate and risk rows retain REQUIRES_CLINICIAN_REVIEW. Failed/stale validation
remains a visible separate warning; observed rows with failures or unavailable
references use NOT_MATCHED instead of EVIDENCE_MATCHED. Unknown types fail closed
to a review label. Long reasons and scope explanations moved to native details
elements. Clinical source buttons open those details and highlight the source
without replacing the Evidence-linked note inspector with a derived claim.

Four summary cards show observed signal count, condition candidate count,
missing information count and the shared review status. These count generated
items, not confirmed clinical facts. No schema, persisted snapshot, rule version,
backend gate or evidence validator changes were needed. Internal contextual
references remain available to correction checks and provenance, but are not
rendered as patient-fact citations for missing/suggested items.

Changed UI: `app/static/clinical.js` (new presentation helper), `app/static/app.js`,
`app/static/index.html`, `app/static/style.css`. Added 13 presentation tests in
`tests/test_clinical_presentation.py`, exercising the actual JavaScript helper
against synthetic backend output. These require Node.js (skip if unavailable).

Verification: **69 passed in 18.25 seconds**, including all prior 56 tests and 13
new cases; no skips in this run. Ruff check/format, mypy and Node syntax checks
for both JavaScript files passed. Headless Edge verified all seven semantic
badges, summary counts, closed details, omission of suggestion/missing citations,
visible unsafe warnings, approval blocking, source highlighting, shared
review/audit, replay, correction and rejection. Desktop and mobile screenshots
were inspected; no browser JavaScript errors or horizontal overflow occurred.
Harness: `.runtime/clinical_semantics_browser.py`; screenshots:
`.runtime/clinical-semantics-desktop.png`, `.runtime/clinical-semantics-mobile.png`.
