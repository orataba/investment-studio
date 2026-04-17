from collections.abc import Sequence

from sqlalchemy.orm import Session

from app.db.models.watchlists import (
    InstrumentAttributeDefinition,
    InstrumentAttributeValue,
)
from app.repositories.interfaces import InstrumentAttributeRepository


class InstrumentAttributeService:
    def __init__(self, repository: InstrumentAttributeRepository) -> None:
        self.repository = repository

    def list_definitions(
        self,
        session: Session,
    ) -> Sequence[InstrumentAttributeDefinition]:
        return self.repository.list_definitions(session)

    def get_values_for_asset(
        self,
        session: Session,
        asset_id: str,
    ) -> Sequence[InstrumentAttributeValue]:
        return self.repository.get_values_for_asset(session, asset_id)
