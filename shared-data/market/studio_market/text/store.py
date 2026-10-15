"""Transactional text imports and point-in-time retrieval."""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from pathlib import Path
import os
import tempfile
from urllib.parse import urlsplit
from uuid import uuid4

from sqlalchemy import create_engine, exists, func, or_, select
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from .bundle import canonical_json, digest, load_bundle, source_time, timestamp, validate_document
from .schema import bundles, collection_receipts, document_entities, document_events, entities, versions


def _iso(value):
    if isinstance(value, datetime):
        return value.replace(tzinfo=value.tzinfo or UTC).astimezone(UTC).isoformat()
    return value


def _source_iso(value):
    instant, day, _ = source_time(value)
    return _iso(instant) if instant is not None else (day.isoformat() if day else None)


class TextStore:
    def __init__(self, settings=None, *, database_url: str | None = None, data_root: str | Path | None = None):
        if settings is None:
            from studio_market.config import MarketSettings
            settings = MarketSettings.from_environment(database_url=database_url, data_root=data_root)
        self.data_root = Path(settings.data_root) / "text"
        self.engine = create_engine(settings.database_url.replace("postgresql://", "postgresql+psycopg://", 1), pool_pre_ping=True)
        if self.engine.dialect.name == "sqlite":
            self.engine = self.engine.execution_options(schema_translate_map={"market_text": None})

    def close(self) -> None:
        self.engine.dispose()

    def _insert(self, connection, table, values):
        factory = sqlite_insert if self.engine.dialect.name == "sqlite" else pg_insert
        return connection.execute(factory(table).values(**values).on_conflict_do_nothing())

    def _save_raw(self, sha256: str, payload: bytes) -> Path:
        target = self.data_root / "objects" / sha256[:2] / sha256
        target.parent.mkdir(parents=True, exist_ok=True)
        if target.exists():
            if digest(target.read_bytes()) != sha256:
                raise ValueError("Stored raw content failed its integrity check")
            return target
        descriptor, temporary = tempfile.mkstemp(prefix=".incoming-", dir=target.parent)
        try:
            with os.fdopen(descriptor, "wb") as handle:
                handle.write(payload)
                handle.flush()
                # Public text shares the numeric store's named-reader ACL
                # contract; never change the mode of an existing object.
                os.fchmod(handle.fileno(), 0o640)
                os.fsync(handle.fileno())
            os.replace(temporary, target)
        finally:
            if os.path.exists(temporary):
                os.unlink(temporary)
        return target

    def _write_document(self, connection, document: dict, body: str, received_at: datetime) -> bool:
        record_digest = digest(canonical_json(document))
        values = {
            key: document[key] for key in (
                "document_id", "version_id", "channel_id", "source_name", "title", "url",
                "information_type", "status", "body_sha256", "raw_sha256", "raw_format",
                "content_completeness", "content_warnings", "provenance",
            )
        }
        publication, publication_date, publication_precision = source_time(document.get("published_at"))
        occurrence, occurrence_date, occurrence_precision = source_time(document.get("occurred_at"))
        values.update(
            source_id="text:" + document["version_id"],
            published_at=publication, published_date=publication_date, published_at_precision=publication_precision,
            occurred_at=occurrence, occurred_date=occurrence_date, occurred_at_precision=occurrence_precision,
            observed_at=timestamp(document["observed_at"], required=True),
            received_at=received_at, content_text=body, record_sha256=record_digest,
        )
        result = self._insert(connection, versions, values)
        existing_digest = connection.execute(
            select(versions.c.record_sha256).where(versions.c.version_id == document["version_id"])
        ).scalar_one()
        if existing_digest != record_digest:
            raise ValueError("An existing immutable document version has different content or provenance")
        for entity in document.get("entities", []):
            self._insert(connection, entities, {k: entity[k] for k in ("entity_id", "label", "kind")})
            self._insert(connection, document_entities, {"version_id": document["version_id"], "entity_id": entity["entity_id"]})
        for event_id in document.get("event_ids", []):
            self._insert(connection, document_events, {"version_id": document["version_id"], "event_id": event_id})
        return bool(result.rowcount)

    def imported_bundle_ids(self) -> set[str]:
        """Committed imports, which may be older than external delivery receipts after restore."""
        with self.engine.connect() as connection:
            return set(connection.scalars(select(bundles.c.bundle_id)))

    def import_bundle(self, path: str | Path, *, expected_sha256: str | None = None) -> dict:
        manifest, objects, package_digest = load_bundle(path, expected_sha256)
        received_at = datetime.now(UTC)
        # Validate the entire archive first. Immutable files precede the one database
        # transaction, so a failed import can never expose a partial corpus.
        for document in manifest["documents"]:
            self._save_raw(document["raw_sha256"], objects[f"objects/{document['raw_sha256']}.bin"])
        with self.engine.begin() as connection:
            prior = connection.execute(select(bundles).where(bundles.c.bundle_id == manifest["bundle_id"])).mappings().first()
            if prior is not None:
                if prior["sha256"] != package_digest:
                    raise ValueError("An existing bundle identity has different archive content")
                return {"bundle_id": prior["bundle_id"], "imported": 0, "duplicate": True, "received_at": _iso(prior["received_at"])}
            imported = 0
            for document in manifest["documents"]:
                body = objects[f"objects/{document['body_sha256']}.txt"].decode("utf-8")
                imported += self._write_document(connection, document, body, received_at)
            for receipt in manifest["coverage"].get("fmp_sources", []):
                values = self._collection_values(receipt, received_at=received_at)
                self._insert(connection, collection_receipts, values)
                existing = connection.execute(select(collection_receipts).where(
                    collection_receipts.c.receipt_id == values["receipt_id"])).mappings().one()
                # Delivery time belongs to this installation. Every other field
                # belongs to the immutable provider-check receipt.
                for key, value in values.items():
                    if key == "received_at":
                        continue
                    stored = existing[key]
                    if key == "checked_at":
                        stored, value = _iso(stored), _iso(value)
                    if stored != value:
                        raise ValueError("An existing immutable collection receipt has different content")
            self._insert(connection, bundles, {
                "bundle_id": manifest["bundle_id"], "sha256": package_digest,
                "producer": manifest["producer"], "window_start": timestamp(manifest.get("window_start")),
                "window_end": timestamp(manifest["window_end"], required=True),
                "received_at": received_at, "document_count": len(manifest["documents"]),
                "coverage": manifest["coverage"],
            })
        return {"bundle_id": manifest["bundle_id"], "imported": imported, "duplicate": False, "received_at": received_at.isoformat()}

    def capture_public_source(self, source: dict, *, received_at: datetime | None = None,
                              origin: str = "fetched") -> dict:
        """Retain public text; author-provided text never impersonates a fetch."""
        if origin not in {"fetched", "author_provided"}:
            raise ValueError("Unknown public-source capture origin")
        url = str(source["url"]).strip()
        parsed = urlsplit(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname or parsed.username or parsed.password:
            raise ValueError("Captured public source requires an HTTP(S) URL without credentials")
        body = source.get("text", "")
        if not isinstance(body, str):
            raise ValueError("Captured public source text must be a string")
        observed = timestamp(source.get("retrieved_at"), required=True)
        received = timestamp(received_at, required=True) if received_at is not None else datetime.now(UTC)
        raw = body.encode("utf-8")
        body_digest = digest(raw)
        identity = digest(url.encode("utf-8"))
        provenance = {
            "collector": "investment-studio", "original_source_id": source.get("source_id"),
            "discovered_at": source.get("discovered_at"), "date_evidence": source.get("date_evidence"),
            "coverage": source.get("coverage"),
            "raw_capture_kind": "fetched_text" if origin == "fetched" else "author_provided_text",
            **({"locator": source.get("locator"), "body_kind": source.get("body_kind"),
                "verification": "author_attested; not fetched or text-matched by importer"}
               if origin == "author_provided" else {}),
        }
        document = {
            "document_id": ("studio-url-" if origin == "fetched" else "studio-author-url-") + identity,
            "channel_id": parsed.hostname.lower(), "source_name": parsed.hostname.lower(),
            "title": str(source.get("title") or url), "url": url,
            "information_type": "public_article", "status": "active",
            "published_at": _source_iso(source.get("published_at")),
            "occurred_at": _source_iso(source.get("occurred_at")),
            "observed_at": observed.isoformat(), "body_sha256": body_digest,
            "raw_sha256": body_digest, "raw_format": "text/plain",
            "content_completeness": "unavailable" if not body.strip() else ("source_excerpt" if source.get("text_truncated") else "full_text"),
            "content_warnings": ["text_truncated"] if source.get("text_truncated") else [],
            "entities": [], "event_ids": [], "provenance": provenance,
        }
        version_material = {key: document[key] for key in ("document_id", "title", "body_sha256", "published_at", "occurred_at", "content_completeness")}
        # The same latest source state is reusable across research runs. A later
        # correction reverting to old wording is a new observation, not the old version.
        document["version_id"] = ("studio-web-" if origin == "fetched" else "studio-author-") + digest(canonical_json({**version_material, "observed_at": observed.isoformat()}))
        validate_document(document)
        self._save_raw(body_digest, raw)
        with self.engine.begin() as connection:
            latest = connection.execute(select(versions).where(
                versions.c.document_id == document["document_id"], versions.c.observed_at <= observed,
            ).order_by(versions.c.observed_at.desc(), versions.c.version_id.desc()).limit(1)).mappings().first()
            if latest is not None:
                latest_material = {key: latest[key] for key in ("document_id", "title", "body_sha256", "content_completeness")}
                for key, day in (("published_at", "published_date"), ("occurred_at", "occurred_date")):
                    latest_material[key] = latest[day].isoformat() if latest[day] else _iso(latest[key])
                if latest_material == version_material:
                    return self._row(connection, latest)
            self._write_document(connection, document, body, received)
        return self.read("text:" + document["version_id"])

    def _latest(self, as_of: datetime):
        ranked = select(
            versions,
            func.row_number().over(partition_by=versions.c.document_id, order_by=(versions.c.observed_at.desc(), versions.c.version_id.desc())).label("version_rank"),
        ).where(versions.c.observed_at <= as_of, versions.c.received_at <= as_of).subquery()
        return ranked

    def capture_provider_document(self, document: dict, body: str, raw: bytes) -> dict:
        """Keep the complete provider response separately from extracted source text.

        A repeated observation reuses the latest identical state. A correction,
        including a reversion, receives a new immutable version and real clock.
        """
        document = dict(document)
        observed = timestamp(document["observed_at"], required=True)
        document.update(body_sha256=digest(body.encode("utf-8")), raw_sha256=digest(raw))
        material = {key: value for key, value in document.items() if key not in {"version_id", "observed_at"}}
        document["version_id"] = "fmp-text-" + digest(canonical_json({**material, "observed_at": observed.isoformat()}))
        validate_document(document)
        self._save_raw(document["raw_sha256"], raw)
        with self.engine.begin() as connection:
            latest = connection.execute(select(versions).where(
                versions.c.document_id == document["document_id"], versions.c.observed_at <= observed,
            ).order_by(versions.c.observed_at.desc(), versions.c.version_id.desc()).limit(1)).mappings().first()
            if latest is not None:
                previous = self._row(connection, latest)
                previous_material = {key: previous.get(key) for key in material}
                if previous_material == material:
                    return previous
            self._write_document(connection, document, body, datetime.now(UTC))
        return self.read("text:" + document["version_id"])

    @staticmethod
    def _collection_values(receipt: dict, *, received_at=None) -> dict:
        from .fmp import CHANNELS
        values = {key: receipt[key] for key in ("symbol", "channel_id", "checked_at", "window_start",
                  "window_end", "status", "document_count", "content_scope", "error_type", "details")}
        values["receipt_id"] = receipt.get("receipt_id") or uuid4().hex
        values["checked_at"] = timestamp(values["checked_at"], required=True)
        values["received_at"] = received_at or datetime.now(UTC)
        for key in ("window_start", "window_end"):
            if isinstance(values[key], str):
                values[key] = date.fromisoformat(values[key])
        if (values["status"] not in {"ready", "partial", "failed"}
                or values["channel_id"] not in CHANNELS or not values["symbol"]
                or values["document_count"] < 0 or values["window_start"] > values["window_end"]):
            raise ValueError("Unsupported text collection status")
        return values

    def record_collection(self, receipt: dict) -> dict:
        values = self._collection_values(receipt)
        with self.engine.begin() as connection:
            connection.execute(collection_receipts.insert().values(**values))
        return {**values, "checked_at": _iso(values["checked_at"]),
                "received_at": _iso(values["received_at"]),
                "window_start": values["window_start"].isoformat(), "window_end": values["window_end"].isoformat()}

    def get_fmp_coverage(self, symbols: list[str], *, as_of: datetime | None = None) -> dict:
        from .fmp import CHANNELS
        cutoff = timestamp(as_of, required=True) if as_of is not None else datetime.now(UTC)
        selected = sorted(set(symbols))
        ranked = select(collection_receipts, func.row_number().over(
            partition_by=(collection_receipts.c.symbol, collection_receipts.c.channel_id),
            order_by=(collection_receipts.c.checked_at.desc(), collection_receipts.c.receipt_id.desc()),
        ).label("rank")).where(collection_receipts.c.symbol.in_(selected),
                              collection_receipts.c.checked_at <= cutoff,
                              collection_receipts.c.received_at <= cutoff).subquery()
        with self.engine.connect() as connection:
            rows = {(row["symbol"], row["channel_id"]): dict(row) for row in connection.execute(
                select(ranked).where(ranked.c.rank == 1)).mappings()}
        result = []
        for symbol in selected:
            for channel, definition in CHANNELS.items():
                row = rows.get((symbol, channel))
                if row is None:
                    result.append({"symbol": symbol, "channel_id": channel, "status": "not_collected",
                                   "checked_at": None, "window_start": None, "window_end": None,
                                   "document_count": None, "content_scope": definition["content_scope"]})
                    continue
                row.pop("rank", None)
                row["checked_at"] = _iso(row["checked_at"])
                row["received_at"] = _iso(row["received_at"])
                for key in ("window_start", "window_end"):
                    row[key] = row[key].isoformat()
                result.append(row)
        return {"sources": result, "as_of": cutoff.isoformat()}

    def last_successful_collection(self, symbol: str, channel_id: str) -> dict | None:
        with self.engine.connect() as connection:
            row = connection.execute(select(collection_receipts).where(
                collection_receipts.c.symbol == symbol, collection_receipts.c.channel_id == channel_id,
                collection_receipts.c.status == "ready",
            ).order_by(collection_receipts.c.checked_at.desc(), collection_receipts.c.receipt_id.desc()).limit(1)).mappings().first()
        return dict(row) if row else None

    def _row(self, connection, row) -> dict:
        return self._rows(connection, [row])[0]

    def _rows(self, connection, rows) -> list[dict]:
        results = [dict(row) for row in rows]
        if not results:
            return []
        version_ids = [row["version_id"] for row in results]
        related_entities = {version_id: [] for version_id in version_ids}
        for row in connection.execute(select(document_entities.c.version_id, entities)
                .join(document_entities).where(document_entities.c.version_id.in_(version_ids))
                .order_by(entities.c.entity_id)).mappings():
            entity = dict(row)
            related_entities[entity.pop("version_id")].append(entity)
        related_events = {version_id: [] for version_id in version_ids}
        for version_id, event_id in connection.execute(select(document_events)
                .where(document_events.c.version_id.in_(version_ids))
                .order_by(document_events.c.event_id)):
            related_events[version_id].append(event_id)
        for result in results:
            result.pop("version_rank", None)
            result.pop("record_sha256", None)
            for key in ("published_at", "occurred_at", "observed_at", "received_at"):
                result[key] = _iso(result[key])
            for key, date_key in (("published_at", "published_date"), ("occurred_at", "occurred_date")):
                day = result.pop(date_key)
                if day is not None:
                    result[key] = day.isoformat()
            result["withdrawn"] = result["status"] in {"withdrawn", "superseded"}
            result["entities"] = related_entities[result["version_id"]]
            result["event_ids"] = related_events[result["version_id"]]
            result["raw_path"] = str(self.data_root / "objects" / result["raw_sha256"][:2] / result["raw_sha256"])
        return results

    def search(
        self, query: str = "", *, entities: list[str] | None = None,
        channel_ids: list[str] | None = None,
        published_after: datetime | None = None, published_before: datetime | None = None,
        observed_after: datetime | None = None, received_after: datetime | None = None, as_of: datetime | None = None,
        limit: int = 50, offset: int = 0, include_withdrawn: bool = False,
    ) -> dict:
        if not isinstance(limit, int) or limit < 1 or not isinstance(offset, int) or offset < 0:
            raise ValueError("Text pagination requires a positive limit and nonnegative offset")
        cutoff = timestamp(as_of, required=True) if as_of is not None else datetime.now(UTC)
        latest = self._latest(cutoff)
        conditions = [latest.c.version_rank == 1]
        if channel_ids:
            conditions.append(latest.c.channel_id.in_(channel_ids))
        if not include_withdrawn:
            conditions.append(latest.c.status.not_in(("withdrawn", "superseded")))
        if published_after is not None:
            lower = timestamp(published_after)
            # Include date-only records only when their whole source day lies
            # inside the requested date window; partial-day cutoffs stay uncertain.
            first_day = published_after.date() + (timedelta(days=1) if published_after.timetz().replace(tzinfo=None) != time.min else timedelta())
            conditions.append(or_(latest.c.published_at >= lower, latest.c.published_date >= first_day))
        if published_before is not None:
            conditions.append(or_(latest.c.published_at < timestamp(published_before), latest.c.published_date < published_before.date()))
        if observed_after is not None:
            conditions.append(latest.c.observed_at >= timestamp(observed_after))
        if received_after is not None:
            conditions.append(latest.c.received_at >= timestamp(received_after))
        if entities:
            conditions.append(exists(select(1).where(
                document_entities.c.version_id == latest.c.version_id,
                document_entities.c.entity_id.in_(entities),
            )))
        for term in query.split():
            escaped = term.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
            conditions.append(or_(latest.c.title.ilike(f"%{escaped}%", escape="\\"), latest.c.content_text.ilike(f"%{escaped}%", escape="\\")))
        # Rank/filter exactly as above, but carry only identities and sort keys
        # through pagination. Fetch full bodies/provenance for the selected page.
        page = select(latest.c.version_id, latest.c.document_id,
            func.coalesce(latest.c.published_at, latest.c.observed_at).label("sort_time"),
        ).where(*conditions).order_by(
            func.coalesce(latest.c.published_at, latest.c.observed_at).desc(), latest.c.document_id,
        ).limit(limit).offset(offset).subquery()
        statement = select(versions).join(page, versions.c.version_id == page.c.version_id).order_by(
            page.c.sort_time.desc(), page.c.document_id)
        with self.engine.connect() as connection:
            total = connection.execute(select(func.count()).select_from(latest).where(*conditions)).scalar_one()
            rows = self._rows(connection, connection.execute(statement).mappings())
        return {"rows": rows, "total": total, "limit": limit, "offset": offset, "coverage": self.get_coverage(as_of=cutoff), "as_of": cutoff.isoformat()}

    def read(self, document_id: str, *, version_id: str | None = None, as_of: datetime | None = None) -> dict | None:
        cutoff = timestamp(as_of, required=True) if as_of is not None else datetime.now(UTC)
        if document_id.startswith("text:"):
            version_id = document_id.split(":", 1)[1]
        conditions = [versions.c.observed_at <= cutoff, versions.c.received_at <= cutoff]
        if version_id is not None:
            conditions.append(versions.c.version_id == version_id)
            if not document_id.startswith("text:"):
                conditions.append(versions.c.document_id == document_id)
        else:
            conditions.append(versions.c.document_id == document_id)
        with self.engine.connect() as connection:
            row = connection.execute(select(versions).where(*conditions).order_by(versions.c.observed_at.desc(), versions.c.version_id.desc()).limit(1)).mappings().first()
            return None if row is None else self._row(connection, row)

    def get_coverage(self, *, as_of: datetime | None = None) -> dict:
        cutoff = timestamp(as_of, required=True) if as_of is not None else datetime.now(UTC)
        with self.engine.connect() as connection:
            receipts = connection.execute(select(bundles).where(bundles.c.received_at <= cutoff,
                bundles.c.producer != "investment-studio-fmp").order_by(bundles.c.window_end.desc(), bundles.c.bundle_id.desc())).mappings().all()
            clocks = connection.execute(select(func.max(versions.c.received_at), func.max(versions.c.observed_at)).where(versions.c.received_at <= cutoff, versions.c.observed_at <= cutoff)).one()
        latest = dict(receipts[0]) if receipts else None
        return {
            "bundle_count": len(receipts), "latest_received_at": _iso(clocks[0]),
            "latest_source_observed_at": _iso(clocks[1]),
            "latest_bundle_window_end": _iso(latest["window_end"]) if latest else None,
            "sources": latest["coverage"].get("sources", []) if latest else [],
            "export_coverage": latest["coverage"] if latest else {},
        }
