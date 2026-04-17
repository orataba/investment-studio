from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.db.models.manual_profiles import AssetManualProfile


class SQLAlchemyAssetManualProfileRepository:
    def get(self, session: Session, asset_id: str) -> AssetManualProfile | None:
        return session.get(AssetManualProfile, asset_id)

    def upsert(
        self,
        session: Session,
        *,
        asset_id: str,
        people_payload_json: dict[str, object] | None = None,
        strategy_payload_json: dict[str, object] | None = None,
        price_payload_json: dict[str, object] | None = None,
        documents_payload_json: dict[str, object] | None = None,
        research_payload_json: dict[str, object] | None = None,
        nav_settings_json: dict[str, object] | None = None,
        updated_by: str | None = None,
    ) -> AssetManualProfile:
        record = self.get(session, asset_id)
        now = datetime.now(UTC).replace(microsecond=0)
        if record is None:
            record = AssetManualProfile(
                asset_id=asset_id,
                people_payload_json=people_payload_json or {},
                strategy_payload_json=strategy_payload_json or {},
                price_payload_json=price_payload_json or {},
                documents_payload_json=documents_payload_json or {},
                research_payload_json=research_payload_json or {},
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
        if research_payload_json is not None:
            record.research_payload_json = research_payload_json
        if nav_settings_json is not None:
            record.nav_settings_json = nav_settings_json
        record.updated_at = now
        record.updated_by = updated_by
        session.flush()
        return record
