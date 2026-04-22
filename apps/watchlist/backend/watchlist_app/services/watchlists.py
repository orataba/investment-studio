from collections.abc import Sequence

from sqlalchemy.orm import Session

from watchlist_app.db.models.watchlists import Watchlist, WatchlistView
from watchlist_app.repositories.interfaces import WatchlistRepository


class WatchlistService:
    def __init__(self, repository: WatchlistRepository) -> None:
        self.repository = repository

    def list_watchlists(self, session: Session) -> Sequence[Watchlist]:
        return self.repository.list(session)

    def get_watchlist(self, session: Session, watchlist_id: str) -> Watchlist | None:
        return self.repository.get(session, watchlist_id)

    def list_views(self, session: Session, watchlist_id: str) -> Sequence[WatchlistView]:
        return self.repository.list_views(session, watchlist_id)
