"""Attempt-bound risk tool receipts. Callers hold the parent run row lock."""
from sqlalchemy import select
from watchlist_app.db.models.workbench import ResearchRiskReadState


def risk_read_state(session, run_id, attempt):
    row = session.get(ResearchRiskReadState, run_id, populate_existing=True)
    if row is None:
        row = ResearchRiskReadState(run_id=run_id, attempt=attempt, delivered_pages=[])
        session.add(row)
        session.flush([row])
    elif row.attempt != attempt:
        row.attempt, row.delivered_pages, row.submitted_review = attempt, [], None
    return row


def get_risk_submission(session, run_id, attempt=None):
    query = select(ResearchRiskReadState.submitted_review).where(ResearchRiskReadState.run_id == run_id)
    if attempt is not None:
        query = query.where(ResearchRiskReadState.attempt == attempt)
    return session.scalar(query)
