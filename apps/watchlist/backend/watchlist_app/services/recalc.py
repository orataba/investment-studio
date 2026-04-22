from collections.abc import Sequence

from sqlalchemy.orm import Session

from watchlist_app.db.models.recalc import RecalcJob
from watchlist_app.repositories.interfaces import RecalcJobRepository


class RecalcJobService:
    def __init__(self, repository: RecalcJobRepository) -> None:
        self.repository = repository

    def list_recent(self, session: Session, limit: int = 100) -> Sequence[RecalcJob]:
        return self.repository.list_recent(session, limit=limit)

    def get(self, session: Session, job_id: str) -> RecalcJob | None:
        return self.repository.get(session, job_id)
