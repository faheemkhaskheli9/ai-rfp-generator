"""Evaluate one persisted draft against the deterministic rubric.

Usage:
    PYTHONPATH=src python scripts/evaluate_draft.py <draft_id>
"""

from __future__ import annotations

import sys

from ai_rfp_generator.db import DraftSection, Fact, make_engine, make_session_factory
from ai_rfp_generator.evaluation import replace_evaluation_scores


def main() -> int:
    if len(sys.argv) != 2:
        print("usage: PYTHONPATH=src python scripts/evaluate_draft.py <draft_id>")
        return 2

    draft_id = int(sys.argv[1])
    session_factory = make_session_factory(make_engine())

    with session_factory() as session:
        draft = session.get(DraftSection, draft_id)
        if draft is None:
            print(f"draft section {draft_id} not found")
            return 1

        facts = (
            session.query(Fact)
            .filter(Fact.requirement_id == draft.requirement_id)
            .order_by(Fact.id)
            .all()
        )
        scores = replace_evaluation_scores(session, draft, facts)
        session.commit()

        for score in sorted(scores, key=lambda item: item.criterion):
            print(f"{score.criterion}: {score.score}/100 — {score.detail}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
