from __future__ import annotations

from datetime import UTC, date, datetime
from typing import cast

from sqlalchemy import select
from sqlalchemy.orm import Session

from watchlist_app.db.models.research import (
    InstrumentResearchNote,
    InstrumentResearchNoteRevision,
    InstrumentResearchProfile,
    InstrumentResearchProfileRevision,
)


PROFILE_TEXT_FIELDS = (
    "thesis",
    "current_view",
    "why_now",
    "edge_assessment",
    "valuation_framework",
    "catalysts",
    "key_risks",
    "disconfirming_evidence",
    "open_questions",
    "monitoring_plan",
    "people_assessment",
    "portfolio_role",
    "time_horizon",
    "decision_rationale",
    "primary_analyst",
    "dd_status",
    "odd_status",
    "ic_status",
)


class SQLAlchemyInstrumentResearchRepository:
    def get_profile(
        self,
        session: Session,
        instrument_id: str,
    ) -> InstrumentResearchProfile | None:
        return session.get(InstrumentResearchProfile, instrument_id)

    def list_profiles(
        self,
        session: Session,
        instrument_ids: list[str],
    ) -> list[InstrumentResearchProfile]:
        if not instrument_ids:
            return []
        return list(
            session.scalars(
                select(InstrumentResearchProfile).where(
                    InstrumentResearchProfile.instrument_id.in_(instrument_ids)
                )
            ).all()
        )

    def upsert_profile(
        self,
        session: Session,
        *,
        instrument_id: str,
        values: dict[str, object],
        updated_by: str | None,
    ) -> InstrumentResearchProfile:
        now = datetime.now(UTC).replace(microsecond=0)
        normalized_text = {
            field: str(values.get(field) or "").strip()
            for field in PROFILE_TEXT_FIELDS
        }
        next_review_date = cast(date | None, values.get("next_review_date"))
        manual_rating = cast(int | None, values.get("manual_rating"))
        record = self.get_profile(session, instrument_id)
        if record is None:
            record = InstrumentResearchProfile(
                instrument_id=instrument_id,
                revision_number=1,
            )
            session.add(record)
        elif (
            all(getattr(record, field) == value for field, value in normalized_text.items())
            and record.next_review_date == next_review_date
            and record.manual_rating == manual_rating
        ):
            return record
        else:
            record.revision_number += 1
        for field, value in normalized_text.items():
            setattr(record, field, value)
        record.next_review_date = next_review_date
        record.manual_rating = manual_rating
        record.updated_by = updated_by
        record.updated_at = now
        session.flush()
        session.add(
            InstrumentResearchProfileRevision(
                instrument_id=instrument_id,
                revision_number=record.revision_number,
                **{field: getattr(record, field) for field in PROFILE_TEXT_FIELDS},
                next_review_date=record.next_review_date,
                manual_rating=record.manual_rating,
                recorded_at=now,
                recorded_by=updated_by,
            )
        )
        return record

    def list_profile_revisions(
        self,
        session: Session,
        instrument_id: str,
    ) -> list[InstrumentResearchProfileRevision]:
        return list(
            session.scalars(
                select(InstrumentResearchProfileRevision)
                .where(
                    InstrumentResearchProfileRevision.instrument_id == instrument_id
                )
                .order_by(InstrumentResearchProfileRevision.revision_number.desc())
            ).all()
        )

    def list_notes(self, session: Session, instrument_id: str) -> list[InstrumentResearchNote]:
        statement = (
            select(InstrumentResearchNote)
            .where(
                InstrumentResearchNote.instrument_id == instrument_id,
                InstrumentResearchNote.deleted_at.is_(None),
            )
            .order_by(
                InstrumentResearchNote.note_date.desc(),
                InstrumentResearchNote.created_at.desc(),
                InstrumentResearchNote.note_id,
            )
        )
        return list(session.scalars(statement).all())

    def list_notes_for_instruments(
        self,
        session: Session,
        instrument_ids: list[str],
    ) -> list[InstrumentResearchNote]:
        if not instrument_ids:
            return []
        return list(
            session.scalars(
                select(InstrumentResearchNote).where(
                    InstrumentResearchNote.instrument_id.in_(instrument_ids),
                    InstrumentResearchNote.deleted_at.is_(None),
                )
            ).all()
        )

    def get_note(
        self,
        session: Session,
        instrument_id: str,
        note_id: str,
    ) -> InstrumentResearchNote | None:
        return session.scalar(
            select(InstrumentResearchNote).where(
                InstrumentResearchNote.instrument_id == instrument_id,
                InstrumentResearchNote.note_id == note_id,
                InstrumentResearchNote.deleted_at.is_(None),
            )
        )

    def create_note(
        self,
        session: Session,
        *,
        instrument_id: str,
        note_id: str,
        values: dict[str, object],
        updated_by: str | None,
    ) -> InstrumentResearchNote:
        now = datetime.now(UTC).replace(microsecond=0)
        record = InstrumentResearchNote(
            instrument_id=instrument_id,
            note_id=note_id,
            note_date=cast(date, values["note_date"]),
            note_type=str(values["note_type"]),
            title=str(values["title"]).strip(),
            summary=str(values.get("summary") or "").strip(),
            body=str(values.get("body") or "").strip(),
            importance=str(values["importance"]),
            tags_json=list(values.get("tags") or []),
            source_refs=str(values.get("source_refs") or "").strip(),
            people=str(values.get("people") or "").strip(),
            author=str(values.get("author") or "").strip(),
            follow_up_date=cast(date | None, values.get("follow_up_date")),
            revision_number=1,
            created_at=now,
            updated_at=now,
            updated_by=updated_by,
        )
        session.add(record)
        session.flush()
        self._record_note_revision(
            session,
            record=record,
            change_type="create",
            recorded_at=now,
            recorded_by=updated_by,
        )
        return record

    def update_note(
        self,
        session: Session,
        *,
        record: InstrumentResearchNote,
        values: dict[str, object],
        updated_by: str | None,
    ) -> InstrumentResearchNote:
        normalized = {
            "note_date": cast(date, values["note_date"]),
            "note_type": str(values["note_type"]),
            "title": str(values["title"]).strip(),
            "summary": str(values.get("summary") or "").strip(),
            "body": str(values.get("body") or "").strip(),
            "importance": str(values["importance"]),
            "tags_json": list(values.get("tags") or []),
            "source_refs": str(values.get("source_refs") or "").strip(),
            "people": str(values.get("people") or "").strip(),
            "author": str(values.get("author") or "").strip(),
            "follow_up_date": cast(date | None, values.get("follow_up_date")),
        }
        if all(getattr(record, field) == value for field, value in normalized.items()):
            return record
        for field, value in normalized.items():
            setattr(record, field, value)
        now = datetime.now(UTC).replace(microsecond=0)
        record.revision_number += 1
        record.updated_at = now
        record.updated_by = updated_by
        session.flush()
        self._record_note_revision(
            session,
            record=record,
            change_type="update",
            recorded_at=now,
            recorded_by=updated_by,
        )
        return record

    def delete_note(
        self,
        session: Session,
        record: InstrumentResearchNote,
        *,
        deleted_by: str | None,
    ) -> None:
        now = datetime.now(UTC).replace(microsecond=0)
        record.revision_number += 1
        record.deleted_at = now
        record.deleted_by = deleted_by
        record.updated_at = now
        record.updated_by = deleted_by
        self._record_note_revision(
            session,
            record=record,
            change_type="delete",
            recorded_at=now,
            recorded_by=deleted_by,
        )
        session.flush()

    def list_note_revisions(
        self,
        session: Session,
        instrument_id: str,
    ) -> list[InstrumentResearchNoteRevision]:
        return list(
            session.scalars(
                select(InstrumentResearchNoteRevision)
                .where(InstrumentResearchNoteRevision.instrument_id == instrument_id)
                .order_by(
                    InstrumentResearchNoteRevision.recorded_at.desc(),
                    InstrumentResearchNoteRevision.note_id,
                    InstrumentResearchNoteRevision.revision_number.desc(),
                )
            ).all()
        )

    @staticmethod
    def _record_note_revision(
        session: Session,
        *,
        record: InstrumentResearchNote,
        change_type: str,
        recorded_at: datetime,
        recorded_by: str | None,
    ) -> None:
        session.add(
            InstrumentResearchNoteRevision(
                instrument_id=record.instrument_id,
                note_id=record.note_id,
                revision_number=record.revision_number,
                change_type=change_type,
                note_date=record.note_date,
                note_type=record.note_type,
                title=record.title,
                summary=record.summary,
                body=record.body,
                importance=record.importance,
                tags_json=list(record.tags_json or []),
                source_refs=record.source_refs,
                people=record.people,
                author=record.author,
                follow_up_date=record.follow_up_date,
                recorded_at=recorded_at,
                recorded_by=recorded_by,
            )
        )
