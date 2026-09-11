# Quick demo UX — 2026-09-11

Presentation-only update to `app/static/index.html`, `app/static/app.js`,
`app/static/style.css`. No backend, API, DB schema, clinical rules or review
conditions changed. Existing advanced operations remain available.

- A single **샘플 데모 실행** button calls the existing Scenario E workflow.
  The scenario selector and session/run/custom input controls remain under
  **고급 설정**. The dedicated sample button always runs E, regardless of the
  advanced scenario selection.
- The four-step guide uses actual UI state: start; processing/result inspection;
  clinician review after evidence inspection; approved/rejected result and Audit.
  It does not infer clinical decisions or add review prerequisites. Final audit
  guidance waits for the audit response; changing run/review status clears that
  UI marker. Failed jobs and unsupported results receive appropriate next-action
  guidance without changing the existing approval gate.
- Current job status remains prominent; per-stage detail and correction history
  are collapsed. Desktop pairs clinical support with review/audit and validation.
  Narrow screens stack the same content without omitting it.
- Replay, Privacy Boundary and Technical Trace are native collapsed details.
  Sidebar links open the corresponding section. IDs, raw provenance, health
  status, events, source inspection and all existing actions remain available.
- README includes a four-action walkthrough and two real 1440×1100 viewport
  captures: `demo-quick-start.png` and `demo-quick-review.png`. These show actual
  synthetic results, not simulated outcomes.

Verification: full pytest (69 cases), Ruff check/format, mypy and JavaScript
syntax checks passed. Headless Edge checked one-click Scenario E, guide stages
1–4, collapsed advanced sections, existing source/semantic/approval protections,
saved approved run reopening, rejection/new-run states, replay, correction,
privacy/trace availability, responsive columns and no horizontal overflow or
JavaScript errors. Harness: `.runtime/quick_demo_browser.py`.
