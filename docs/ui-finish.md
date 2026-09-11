# Review console UI finish — 2026-09-11

Changed only `app/static/index.html`, `app/static/app.js`, and
`app/static/style.css` for presentation. Backend Python files, migrations and
project configuration were compared with SHA-256 hashes captured at the start
of this task: no changes. No tests were removed or modified.

## Presentation

- Removed sidebar technology stack and portfolio promotion, and the footer's
  REST / PostgreSQL outbox / Redis Streams / Worker / WebSocket architecture line.
- Moved existing health checks, connection status, event history and API reference
  link inside Technical Trace. No health checks or live event handling were removed.
- Preserved all trace fields, raw provenance and validation snapshots; moved
  clinical provider/model/rule identifiers and generation/review timestamps from
  the clinical panel into named trace fields. Reviewer and review state remain
  visible in the clinical panel as well.
- Korean headings, navigation, input controls, review buttons and primary guidance.
  English state codes and technical trace field names remain unchanged.
- Clinical support uses two columns above 1000px: observations, risk and missing
  information on the left; candidates, questions, assessment and review proposals
  on the right. Mobile uses one column. Groups use compact separators rather than
  additional boxed cards. Item details start closed; source inspection can open them.
- Pending, approved and rejected states use the existing amber/green/red palette.
  The shared review status remains visible in the header and summary. Completed
  reviews no longer repeat a pending-review badge on candidate/risk rows; those
  rows still retain their candidate/rule meaning. Backend item states are unchanged.
- Synthetic-data scope, clinician review requirement, non-diagnostic scope and
  risk-detection limitations remain visible.

## Verification

| Check | Result |
|---|---|
| Full pytest, including clinical and presentation cases | 69 passed in 23.54s |
| Ruff check | Passed |
| Ruff format check | Passed, 26 files |
| mypy | Passed, 14 source files |
| Node syntax checks: app.js and clinical.js | Passed |
| Browser: headless Microsoft Edge | Passed |
| Backend/config/migration hash comparison | No changes |

Browser checks used the existing isolated synthetic demo on port 8011. Verified
Korean headings, absence of technology names outside Technical Trace, moved
health/event/API elements, all seven semantic badges, real summary counts,
missing/suggestion source-button omission, and initially closed item details.
Checked real source highlighting, failed-validation approval blocking, review
audit, original-input replay/comparison, correction handling and rejection.

Scenario E was approved, then reopened from saved sessions/runs after a page
reload: APPROVED remained visible in the clinical and review headers. Replaying
returned to REQUIRES_CLINICIAN_REVIEW; rejection displayed REJECTED; another new
Scenario E returned to pending. Trace fields were verified after reopening.

Desktop (1440px) and mobile (390px) layouts were inspected. Two-column/one-column
layout assertions passed. No JavaScript errors or horizontal overflow occurred.
The final mobile adjustment was checked separately: the summary review code fits
on one line. Harness: `.runtime/ui_final_browser.py`. Screenshots:
`.runtime/ui-final-clinical-desktop.png`, `.runtime/ui-final-clinical-mobile.png`.
