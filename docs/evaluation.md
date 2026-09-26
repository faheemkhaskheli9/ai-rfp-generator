# Evaluation: RFP Generation

The project uses a reproducible deterministic rubric for generated section
drafts. LLM-as-judge evaluation may be added later as a supplemental signal,
but deterministic checks remain useful in CI and do not require credentials.

## Rubric

Each criterion is scored from 0 to 100.

### Grounding

Measures the percentage of claim sentences that contain citations resolving to
facts belonging to the same requirement.

- 100: every claim sentence has at least one resolvable citation.
- 50: half of the claim sentences are grounded.
- 0: no claim sentence is grounded, or the draft is empty.

This score measures traceability, not whether a cited source semantically proves
every nuance of the claim.

### Completeness

Measures lexical coverage between the approved outline section
(title + description) and the generated draft.

- Important outline keywords are extracted deterministically.
- Score = percentage of those keywords appearing in the draft.

This is intentionally simple and reproducible. It is a regression signal, not a
substitute for human review.

### Tone

Starts at 100 and deducts 20 points per configured risky/absolute phrase found
in the draft, including wording such as:

- guarantee / guaranteed
- 100% secure
- zero downtime
- best in class
- unmatched
- never fails

The score is bounded at zero.

## Persistence

Scores are stored per immutable `DraftSection`, so drafts generated with
different strategies can be compared side by side without overwriting one
another.

The API exposes:

```text
POST /draft-sections/{draft_id}/evaluate
GET  /draft-sections/{draft_id}/evaluation
```

Re-running evaluation replaces stale scores for that same immutable draft.

## Reproducing an Evaluation

Set `DATABASE_URL` when needed, then run:

```bash
PYTHONPATH=src python scripts/evaluate_draft.py <draft_id>
```

Example output:

```text
completeness: 75/100 — 6/8 outline keywords appear in the draft.
grounding: 100/100 — 3/3 claim sentences have resolvable citations.
tone: 100/100 — No configured risky or absolute wording detected.
```

## Prompt Regression Suite

Prompt/behavior regression cases are independent of rubric evaluation and run
offline:

```bash
PYTHONPATH=src python scripts/run_prompt_tests.py
```

This is also executed by GitHub Actions.
