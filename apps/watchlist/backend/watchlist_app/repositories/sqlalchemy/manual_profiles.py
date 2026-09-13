from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from watchlist_app.db.models.instruments import InstrumentDetail
from watchlist_app.db.models.manual_profiles import InstrumentManualProfile


class SQLAlchemyInstrumentManualProfileRepository:
    def get(self, session: Session, instrument_id: str) -> InstrumentManualProfile | None:
        return session.get(InstrumentManualProfile, instrument_id)

    def get_for_update(self, session: Session, instrument_id: str) -> InstrumentManualProfile | None:
        # The instrument already exists even before its first manual profile.
        # Lock it before reading JSON so concurrent appends and first inserts
        # cannot replace one another, then refresh any earlier cached snapshot.
        session.execute(select(InstrumentDetail.instrument_id).where(
            InstrumentDetail.instrument_id == instrument_id,
        ).with_for_update())
        return session.get(InstrumentManualProfile, instrument_id, populate_existing=True)

    def upsert(
        self,
        session: Session,
        *,
        instrument_id: str,
        people_payload_json: dict[str, object] | None = None,
        strategy_payload_json: dict[str, object] | None = None,
        price_payload_json: dict[str, object] | None = None,
        documents_payload_json: dict[str, object] | None = None,
        nav_settings_json: dict[str, object] | None = None,
        updated_by: str | None = None,
    ) -> InstrumentManualProfile:
        record = self.get_for_update(session, instrument_id)
        now = datetime.now(UTC).replace(microsecond=0)
        if record is None:
            record = InstrumentManualProfile(
                instrument_id=instrument_id,
                people_payload_json=people_payload_json or {},
                strategy_payload_json=strategy_payload_json or {},
                price_payload_json=price_payload_json or {},
                documents_payload_json=documents_payload_json or {},
                nav_settings_json=nav_settings_json or {},
                updated_at=now,
                updated_by=updated_by,
            )
            session.add(record)
            session.flush()
            return record

        if people_payload_json is not None:
            record.people_payload_json = people_payload_json
        if strategy_payload_json is not None:
            record.strategy_payload_json = strategy_payload_json
        if price_payload_json is not None:
            record.price_payload_json = price_payload_json
        if documents_payload_json is not None:
            record.documents_payload_json = documents_payload_json
        if nav_settings_json is not None:
            record.nav_settings_json = nav_settings_json
        record.updated_at = now
        record.updated_by = updated_by
        session.flush()
        return record
