# Evidence validation

Order of deterministic checks:

1. Empty references or nonexistent sequences → UNSUPPORTED.
2. Any referenced superseded utterance → STALE_EVIDENCE.
3. Explicit numeric/Korean-word hours disagree → CONTRADICTED.
4. Exact text equals one cited utterance → SUPPORTED.
5. Source contains statement as excerpt → PARTIALLY_SUPPORTED.
6. Otherwise → UNSUPPORTED (requires semantic review).

This conservative baseline is reproducible, not a semantic entailment model. A status is a rule result, not clinical validation. An exact quoted diagnostic phrase would still only be a quoted source; the system does not itself diagnose. Providers implement Protocol interfaces so a future semantic validator can replace this baseline after separate evaluation.

`corrects` explicitly identifies a prior active sequence. The original stays immutable, with `superseded_by` pointing forward; new sequence is active. Natural language correction detection is not implemented. Both same-batch and later corrections are supported. Invalid correction causes transaction rollback.

Validation at inference time is persisted. Result reads overlay `current_validation=STALE_EVIDENCE` if source is now superseded. Approval reacquires the session lock and checks current evidence, preventing a correction/approval race. Later correction conservatively invalidates previous approvals in that session and creates audit events. No approval override exists; partial/unsupported/stale/contradicted statements all block approval. Rejection is permitted for review-required notes.

Evidence coverage = number of SUPPORTED statements / total statements. Empty output is schema-invalid; there is no divide-by-zero or misleading empty 100% coverage. These are synthetic rule metrics, not measured clinical or model accuracy.
