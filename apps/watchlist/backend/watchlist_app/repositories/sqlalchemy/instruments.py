from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from watchlist_app.db.models.instruments import InstrumentDetail


def _primary_identifier(shared_instrument: dict[str, object]) -> tuple[str | None, str | None]:
    identifiers = shared_instrument.get("identifiers")
    if not isinstance(identifiers, list):
        return None, None
    primary = next(
        (
            item
            for item in identifiers
            if isinstance(item, dict) and bool(item.get("is_primary"))
        ),
        None,
    )
    fallback = next((item for item in identifiers if isinstance(item, dict)), None)
    candidate = primary or fallback
    if not isinstance(candidate, dict):
        return None, None
    identifier_type = str(candidate.get("identifier_type") or "").strip() or None
    identifier_value = str(candidate.get("identifier_value") or "").strip() or None
    return identifier_type, identifier_value


class SQLAlchemyInstrumentRepository:
    def list_library(self, session: Session) -> list[dict[str, str | None]]:
        stmt = (
            select(InstrumentDetail)
            .where(InstrumentDetail.is_active.is_(True))
            .order_by(InstrumentDetail.instrument_name, InstrumentDetail.instrument_id)
        )
        records = session.scalars(stmt).all()
        return [
            {
                "instrument_id": record.instrument_id,
                "instrument_name": record.instrument_name,
                "instrument_type": record.instrument_type,
                "detail_view_type": record.detail_view_type,
                "primary_identifier": record.primary_identifier_value,
            }
            for record in records
        ]

    def get(self, session: Session, instrument_id: str) -> InstrumentDetail | None:
        return session.get(InstrumentDetail, instrument_id)

    def upsert_minimal(
        self,
        session: Session,
        *,
        instrument_id: str,
        instrument_type: str,
        detail_view_type: str,
        instrument_name: str,
        primary_identifier_type: str | None,
        primary_identifier_value: str | None,
        metadata_json: dict[str, object] | None = None,
    ) -> InstrumentDetail:
        record = self.get(session, instrument_id)
        now = datetime.now(UTC).replace(microsecond=0)
        if record is None:
            record = InstrumentDetail(
                instrument_id=instrument_id,
                instrument_type=instrument_type,
                detail_view_type=detail_view_type,
                instrument_name=instrument_name,
                primary_identifier_type=primary_identifier_type,
                primary_identifier_value=primary_identifier_value,
                is_active=True,
                metadata_json=metadata_json or {},
                created_at=now,
                updated_at=now,
            )
            session.add(record)
            session.flush()
            return record

        record.instrument_type = instrument_type
        record.detail_view_type = detail_view_type
        record.instrument_name = instrument_name
        record.primary_identifier_type = primary_identifier_type
        record.primary_identifier_value = primary_identifier_value
        record.is_active = True
        record.metadata_json = metadata_json or {}
        record.updated_at = now
        session.flush()
        return record

    def upsert_from_shared_instrument(
        self,
        session: Session,
        *,
        shared_instrument: dict[str, object],
        detail_view_type: str,
    ) -> InstrumentDetail:
        instrument_id = str(shared_instrument.get("instrument_id") or "").strip()
        if not instrument_id:
            raise ValueError("shared instrument must include instrument_id")
        identifier_type, identifier_value = _primary_identifier(shared_instrument)
        return self.upsert_minimal(
            session,
            instrument_id=instrument_id,
            instrument_type=str(shared_instrument.get("instrument_type") or "other"),
            detail_view_type=detail_view_type,
            instrument_name=str(shared_instrument.get("instrument_name") or instrument_id),
            primary_identifier_type=identifier_type,
            primary_identifier_value=identifier_value,
            metadata_json={
                "exchange_code": str(shared_instrument.get("exchange_code") or "").strip().upper()
                or None
            },
        )
