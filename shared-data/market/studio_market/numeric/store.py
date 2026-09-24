from __future__ import annotations

import json
import math
import os
from decimal import Decimal
from collections.abc import Iterable, Mapping
from datetime import date, datetime, time, timezone
from pathlib import Path
from uuid import uuid4
from zoneinfo import ZoneInfo

import duckdb
import pyarrow as pa
import pyarrow.parquet as pq
from sqlalchemy import and_, case, create_engine, delete, func, insert, select, update, text
from sqlalchemy.dialects.postgresql import insert as pg_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert

from studio_market.config import MarketSettings
from .datasets import Dataset, DATASETS, dataset as get_dataset
from .schema import batches, current, datasets, files, metadata, snapshots

UTC = timezone.utc


def serializable(value):
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, Decimal):
        return int(value) if value==value.to_integral_value() else float(value)
    if isinstance(value, dict):
        return {k: serializable(v) for k, v in value.items()}
    if isinstance(value, (list, tuple)):
        return [serializable(v) for v in value]
    if isinstance(value, float) and not math.isfinite(value):
        return None
    if hasattr(value, "as_py"):
        return serializable(value.as_py())
    return value


def instant(value, *, naive_zone=UTC) -> datetime:
    if isinstance(value, str):
        value = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if isinstance(value, date) and not isinstance(value, datetime):
        value = datetime.combine(value, time.max)
    if not isinstance(value, datetime):
        raise ValueError("A valid observation timestamp is required")
    return value.replace(tzinfo=naive_zone).astimezone(UTC) if value.tzinfo is None else value.astimezone(UTC)


def cutoff_instant(value) -> datetime:
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00")) if isinstance(value, str) else value
    if not isinstance(parsed, datetime) or parsed.tzinfo is None:
        raise ValueError("as_of must include a timezone")
    return parsed.astimezone(UTC)


def row_key(row: Mapping, keys: tuple[str, ...]) -> str:
    missing = [k for k in keys if k not in row]
    if missing:
        raise ValueError(f"Missing dataset key fields: {', '.join(missing)}")
    return json.dumps([serializable(row[k]) for k in keys], ensure_ascii=False, separators=(",", ":"))


def _day(value) -> date | None:
    if value is None or value == "":
        return None
    if isinstance(value, datetime):
        return value.date()
    if isinstance(value, date):
        return value
    return date.fromisoformat(str(value)[:10])


def _availability(row: Mapping, spec: Dataset, observed: datetime) -> tuple[datetime, str]:
    if row.get("available_at"):
        return instant(row["available_at"]), str(row.get("availability_precision", "timestamp"))
    if spec.historical_use == "since_capture":
        return observed, "capture"
    if row.get("accepted_at"):
        return instant(row["accepted_at"], naive_zone=ZoneInfo("America/New_York")), "timestamp"
    for name in ("filing_date", "ann_date", "declaration_date"):
        if row.get(name):
            zone = ZoneInfo("Asia/Shanghai" if spec.name.startswith("cn_") else "America/New_York")
            return instant(_day(row[name]), naive_zone=zone), "date_end"
    # Date-only history does not acquire a fictitious precise publication clock.
    # The observed-at restriction still prevents revised history entering an earlier as-of query.
    return observed, "capture"


class NumericStore:
    def __init__(self, settings: MarketSettings, *, engine=None):
        self.settings = settings
        self.engine = engine or create_engine(settings.database_url.replace("postgresql://","postgresql+psycopg://",1))
        if self.engine.dialect.name == "sqlite":
            self.engine = self.engine.execution_options(schema_translate_map={"market_data": None})

    def create_schema_for_testing(self):
        if self.engine.dialect.name != "sqlite":
            raise ValueError("Use Alembic migrations for PostgreSQL")
        metadata.create_all(self.engine)

    def close(self):
        self.engine.dispose()

    @staticmethod
    def _register_dataset(conn, spec):
        # Close jobs, scheduled collection and Regime acquisition may all be the
        # first writer for one dataset. Its catalog identity is a single upsert.
        factory = sqlite_insert if conn.dialect.name == "sqlite" else pg_insert
        conn.execute(factory(datasets).values(name=spec.name, description=spec.description)
                     .on_conflict_do_nothing(index_elements=[datasets.c.name]))

    def status(self) -> dict:
        with self.engine.connect() as conn:
            items = conn.execute(select(datasets.c.name, datasets.c.description, datasets.c.updated_at)).mappings().all()
            totals = {r.dataset: r for r in conn.execute(select(batches.c.dataset, func.count().label("batch_count"), func.sum(batches.c.row_count).label("row_count"), func.max(batches.c.published_at).label("last_published_at")).where(batches.c.status == "ready").group_by(batches.c.dataset))}
            failures = conn.execute(select(batches.c.id,batches.c.dataset,batches.c.started_at,batches.c.error).where(batches.c.status == "failed").order_by(batches.c.started_at.desc()).limit(20)).mappings().all()
        return serializable({"pipeline":json.loads((self.settings.data_root/"pipeline-status.json").read_text()) if (self.settings.data_root/"pipeline-status.json").exists() else None,"last_collection":json.loads((self.settings.data_root/"collection-status.json").read_text()) if (self.settings.data_root/"collection-status.json").exists() else None,"datasets": [dict(r, **({"batch_count": totals[r["name"]].batch_count,"row_count": totals[r["name"]].row_count,"last_published_at": totals[r["name"]].last_published_at} if r["name"] in totals else {"batch_count":0,"row_count":0})) for r in items], "recent_failures": [dict(r) for r in failures]})

    def ingest(self, dataset: str, chunks: Iterable, *, source: str, observed_at: datetime | None = None, raw_ref: str | None = None, details: dict | None = None) -> dict:
        """Publish a batch atomically. Each chunk is a Table, RecordBatch or list of rows.

        Every capture is retained, including A -> B -> A. Readers only see files
        and current projections committed in the same PostgreSQL transaction.
        """
        spec = get_dataset(dataset)
        batch_id = str(uuid4())
        started = datetime.now(UTC)
        default_observed = instant(observed_at or started)
        details = dict(details or {})
        with self.engine.begin() as conn:
            self._register_dataset(conn, spec)
            conn.execute(insert(batches).values(id=batch_id,dataset=dataset,source=source,status="writing",started_at=started,row_count=0,details=serializable({k:v for k,v in details.items() if k!="snapshot_scopes"})))
        directory = self.settings.data_root / "numeric" / dataset / batch_id
        published_files = []
        projections: dict[str, dict] = {}
        snapshot_records = {}
        total = 0
        try:
            directory.mkdir(parents=True, exist_ok=False)
            for part, chunk in enumerate(chunks):
                if isinstance(chunk, pa.RecordBatch):
                    chunk = pa.Table.from_batches([chunk])
                incoming = chunk.to_pylist() if isinstance(chunk, pa.Table) else list(chunk)
                if not incoming:
                    continue
                normalized = []
                for item in incoming:
                    row = dict(item)
                    if dataset in ("us_eod_daily", "raw_eod_daily"):
                        close=float(row["close"])
                        adjusted=float(row["adjusted_close"])
                        row["adjustment_factor"]=adjusted/close
                        row["ohlc_adjustment"]="unadjusted" if dataset=="raw_eod_daily" else "split_adjusted"
                        row["adjusted_close_adjustment"]="split_and_dividend_adjusted"
                    observed = instant(row.get("observed_at") or row.get("collected_at") or default_observed)
                    available, precision = _availability(row, spec, observed)
                    key = row_key(row, spec.keys)
                    symbol = str(row.get(spec.symbol) or row.get("symbol") or "")
                    fact_date = _day(row.get(spec.date)) if spec.date else observed.date()
                    snap_at=instant(row.get("snapshot_at") or observed)
                    frequency=str(row.get("estimate_period") or "") if dataset=="analyst_estimates" else ""
                    snap_key=json.dumps([dataset,symbol,frequency],separators=(",",":"))
                    if dataset in ("analyst_estimates","etf_holdings") and row.get("snapshot_complete",details.get("snapshot_complete",True)):
                        snap=snapshot_records.setdefault((snap_key,snap_at),dict(dataset=dataset,scope_key=snap_key,snapshot_at=snap_at,batch_id=batch_id,symbol=symbol,frequency=frequency,row_count=0))
                        snap["row_count"]+=1
                    row.update(snapshot_at=snap_at,_snapshot_key=snap_key,_key=key, _current_key=row_key(row,spec.current_keys) if spec.current_keys else key, symbol=symbol, date=fact_date,
                        observed_at=observed, available_at=available, availability_precision=precision,
                        source=row.get("source") or row.get("source_dataset") or source,
                        raw_ref=row.get("raw_ref") or raw_ref,
                        batch_id=batch_id, row_index=total + len(normalized),
                        source_id=f"numeric:{batch_id}:{total + len(normalized)}",
                        historical_use=row.get("historical_use") or spec.historical_use)
                    normalized.append(row)
                    if spec.current_keys:
                        pkey = row_key(row, spec.current_keys)
                        candidate = dict(key=pkey,symbol=symbol,observed_at=observed,batch_id=batch_id,row_index=row["row_index"],payload=serializable(row))
                        previous = projections.get(pkey)
                        if previous is None or self._projection_rank(candidate) >= self._projection_rank(previous):
                            projections[pkey] = candidate
                table = pa.Table.from_pylist(normalized)
                # Null-only batches must have stable metadata types for union_by_name reads.
                for name, dtype in [("observed_at",pa.timestamp("us",tz="UTC")),("available_at",pa.timestamp("us",tz="UTC")),("snapshot_at",pa.timestamp("us",tz="UTC")),("date",pa.date32()),("raw_ref",pa.string())]:
                    idx = table.schema.get_field_index(name)
                    table = table.set_column(idx,name,table.column(name).cast(dtype))
                target = directory / f"part-{part:06d}.parquet"
                temporary = target.with_suffix(".tmp")
                pq.write_table(table,temporary,compression="zstd",row_group_size=65536)
                with temporary.open("rb") as stream:
                    os.fsync(stream.fileno())
                os.replace(temporary,target)
                dates = [r["date"] for r in normalized if r["date"] is not None]
                published_files.append(dict(batch_id=batch_id,part=part,path=str(target.relative_to(self.settings.data_root)),row_count=len(normalized),first_row=total,last_row=total+len(normalized)-1,min_date=min(dates).isoformat() if dates else None,max_date=max(dates).isoformat() if dates else None,bytes=target.stat().st_size))
                total += len(normalized)
            for scope in details.get("snapshot_scopes",[]):
                symbol=str(scope["symbol"]);frequency=str(scope.get("frequency") or "")
                at=instant(scope.get("snapshot_at") or default_observed)
                key=json.dumps([dataset,symbol,frequency],separators=(",",":"))
                snapshot_records.setdefault((key,at),dict(dataset=dataset,scope_key=key,snapshot_at=at,batch_id=batch_id,symbol=symbol,frequency=frequency,row_count=0))
            with self.engine.begin() as conn:
                if conn.dialect.name == "postgresql":
                    conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:name))"), {"name": "studio_market:"+dataset})
                if published_files:
                    conn.execute(insert(files), published_files)
                if snapshot_records:
                    conn.execute(insert(snapshots),list(snapshot_records.values()))
                self._publish_current(conn,dataset,projections.values(),scope_records=snapshot_records.values())
                now = datetime.now(UTC)
                conn.execute(update(batches).where(batches.c.id==batch_id).values(status="ready",published_at=now,row_count=total))
                conn.execute(update(datasets).where(datasets.c.name==dataset).values(updated_at=now))
            return {"batch_id":batch_id,"dataset":dataset,"status":"ready","row_count":total,"parts":len(published_files)}
        except Exception as exc:
            # Files remain unreferenced forensic evidence; they are never queried.
            with self.engine.begin() as conn:
                conn.execute(update(batches).where(batches.c.id==batch_id).values(status="failed",error=f"{type(exc).__name__}: publication failed",row_count=total))
            raise

    @staticmethod
    def _projection_rank(record):
        value = record["payload"].get("date") or ""
        return (str(value), instant(record["observed_at"]), record["batch_id"], record["row_index"])

    def _publish_current(self, conn, name, projections, *, scope_records=()):
        rows = list(projections)
        if name == "analyst_estimates":
            # A complete capture replaces its symbol/frequency scope, including
            # an empty response. Older imports may neither revive removed periods
            # nor clear a newer scope for another symbol in the same batch.
            scope_keys = sorted({r["scope_key"] for r in scope_records} |
                                {r["payload"]["_snapshot_key"] for r in rows})
            latest = {}
            for begin in range(0, len(scope_keys), 1000):
                keys = scope_keys[begin:begin + 1000]
                latest.update({key: instant(at) for key, at in conn.execute(
                    select(snapshots.c.scope_key, func.max(snapshots.c.snapshot_at))
                    .where(snapshots.c.dataset == name, snapshots.c.scope_key.in_(keys))
                    .group_by(snapshots.c.scope_key))})
                existing = conn.execute(select(current.c.key, current.c.payload)
                    .where(current.c.dataset == name, current.c.payload["_snapshot_key"].as_string().in_(keys)))
                stale = [key for key, payload in existing if payload["_snapshot_key"] in latest
                         and instant(payload["snapshot_at"]) < latest[payload["_snapshot_key"]]]
                if stale:
                    conn.execute(delete(current).where(current.c.dataset == name, current.c.key.in_(stale)))
            rows = [r for r in rows if instant(r["payload"]["snapshot_at"]) == latest.get(r["payload"]["_snapshot_key"])]
        # Current is intentionally bounded (per symbol or estimate horizon), never a
        # second full history. Importing an older batch cannot roll it backwards.
        for begin in range(0,len(rows),1000):
            chunk=rows[begin:begin+1000]
            existing = {r["key"]:dict(r) for r in conn.execute(select(current).where(current.c.dataset==name,current.c.key.in_([r["key"] for r in chunk]))).mappings()}
            chosen=[]
            for row in chunk:
                previous=existing.get(row["key"])
                if previous is None or self._projection_rank(row)>=self._projection_rank(previous):
                    chosen.append(dict(row,dataset=name))
            if chosen:
                conn.execute(delete(current).where(current.c.dataset==name,current.c.key.in_([r["key"] for r in chosen])))
                conn.execute(insert(current),chosen)

    def _paths(self, name: str, start=None, end=None, batch_id=None, batch_ids=None, *, symbols=None):
        query = select(files.c.path).join(batches,batches.c.id==files.c.batch_id).where(batches.c.dataset==name,batches.c.status=="ready")
        if start:
            query=query.where((files.c.max_date>=str(start)) | files.c.max_date.is_(None))
        if end:
            query=query.where((files.c.min_date<=str(end)) | files.c.min_date.is_(None))
        if batch_id:
            query=query.where(batches.c.id==batch_id)
        if batch_ids is not None:
            query=query.where(batches.c.id.in_(batch_ids))
        if name in {"financial_statements", "financial_facts"} and symbols is not None:
            # These three Collector.financials endpoints normalize against the
            # singleton requested symbol before publication (including original
            # captures). Both the statement and its line-item facts are
            # published from that same normalized response. Bulk/imported/
            # unknown batches have no such guarantee.
            # Read JSON scalars, not their text casts, so malformed metadata is
            # retained on both PostgreSQL and SQLite rather than hiding history.
            query = query.add_columns(batches.c.source, batches.c.details["endpoint"],
                                      batches.c.details["parameters"]["symbol"])
            requested = set(symbols)
            endpoints = {"income-statement", "balance-sheet-statement", "cash-flow-statement"}
            with self.engine.connect() as conn:
                paths = []
                for path, source, endpoint, symbol in conn.execute(query):
                    scoped = (source == "fmp" and isinstance(endpoint, str) and endpoint in endpoints
                              and isinstance(symbol, str) and bool(symbol)
                              and symbol == symbol.strip().upper())
                    if not scoped or symbol in requested:
                        paths.append(str(self.settings.data_root / path))
                return paths
        with self.engine.connect() as conn:
            return [str(self.settings.data_root/r[0]) for r in conn.execute(query)]

    def query(self, dataset: str, *, symbols: list[str] | None = None, start: str | None = None, end: str | None = None, as_of: str | datetime | None = None, limit: int = 1000, offset: int = 0, versions: bool = False, batch_id: str | None = None, batch_ids: list[str] | None = None, observed_at: str | datetime | None = None, _latest: bool = False) -> dict:
        get_dataset(dataset)
        if limit < 1 or limit > 100000 or offset < 0:
            raise ValueError("limit must be 1..100000 and offset nonnegative")
        if batch_id is not None and batch_ids is not None:
            raise ValueError("Choose one batch or a batch collection, not both")
        if start: _day(start)
        if end: _day(end)
        cutoff = cutoff_instant(as_of) if as_of else None
        paths=self._paths(dataset,start,end,batch_id,batch_ids,symbols=symbols)
        result={"dataset":dataset,"rows":[],"total":0,"limit":limit,"offset":offset,"provenance":{"as_of":serializable(cutoff),"version_policy":"all_captures" if versions else "latest_observed_per_fact","historical_use":get_dataset(dataset).historical_use}}
        if not paths:
            return result
        clauses=[]; params=[]
        if symbols is not None:
            if not symbols: return result
            clauses.append("symbol IN (SELECT unnest(?))");params.append(symbols)
        if start: clauses.append("date >= ?::DATE");params.append(start)
        if end: clauses.append("date <= ?::DATE");params.append(end)
        if observed_at:
            clauses.append("snapshot_at = ?");params.append(cutoff_instant(observed_at))
        if cutoff:
            clauses.append("observed_at <= ? AND available_at <= ?");params.extend([cutoff,cutoff])
        where=" WHERE "+" AND ".join(clauses) if clauses else ""
        qualify="" if versions else " QUALIFY row_number() OVER (PARTITION BY _key ORDER BY observed_at DESC, batch_id DESC, row_index DESC)=1"

        sql="SELECT * FROM facts"+where+qualify
        if _latest and get_dataset(dataset).current_keys:
            sql="SELECT * FROM ("+sql+") QUALIFY row_number() OVER (PARTITION BY _current_key ORDER BY date DESC NULLS LAST, observed_at DESC, batch_id DESC, row_index DESC)=1"
        with duckdb.connect(":memory:") as db:
            relation=db.from_parquet(paths,union_by_name=True)
            relation.create_view("facts")
            if not versions and dataset in ("analyst_estimates","etf_holdings"):
                chosen=self._snapshot_rows(dataset,symbols,cutoff,limit=1,at=observed_at,batch_id=batch_id,batch_ids=batch_ids)
                if not chosen:return result
                db.register("chosen_snapshots",pa.Table.from_pylist([{ "chosen_scope_key":r["scope_key"],"chosen_snapshot_at":instant(r["snapshot_at"]),"chosen_batch_id":r["batch_id"]} for r in chosen]))
                sql=sql.replace("FROM facts","FROM facts JOIN chosen_snapshots c ON facts._snapshot_key=c.scope_key AND facts.snapshot_at=c.snapshot_at AND facts.batch_id=c.batch_id").replace("SELECT * FROM facts", "SELECT facts.* FROM facts")
                # The chosen table only carries uniquely prefixed join columns.
                sql=sql.replace("c.scope_key","c.chosen_scope_key").replace("c.snapshot_at","c.chosen_snapshot_at").replace("c.batch_id","c.chosen_batch_id")
            result["total"]=db.execute("SELECT count(*) FROM ("+sql+")",params).fetchone()[0]
            cursor=db.execute(sql+" ORDER BY date DESC NULLS LAST, symbol, observed_at DESC, batch_id, row_index LIMIT ? OFFSET ?",[*params,limit,offset])
            names=[d[0] for d in cursor.description]
            result["rows"]=[serializable(dict(zip(names,row))) for row in cursor.fetchall()]
        return result

    def _snapshot_rows(self,dataset,symbols,as_of,limit=2,at=None,batch_id=None,batch_ids=None):
        rank=func.dense_rank().over(partition_by=snapshots.c.scope_key,order_by=snapshots.c.snapshot_at.desc()).label("capture_rank")
        statement=select(snapshots,rank).join(batches,batches.c.id==snapshots.c.batch_id).where(snapshots.c.dataset==dataset,batches.c.status=="ready")
        if symbols is not None:statement=statement.where(snapshots.c.symbol.in_(symbols))
        if as_of:statement=statement.where(snapshots.c.snapshot_at<=cutoff_instant(as_of))
        if at:statement=statement.where(snapshots.c.snapshot_at==cutoff_instant(at))
        if batch_id:statement=statement.where(snapshots.c.batch_id==batch_id)
        if batch_ids is not None:statement=statement.where(snapshots.c.batch_id.in_(batch_ids))
        sub=statement.subquery()
        with self.engine.connect() as conn:
            return [dict(r) for r in conn.execute(select(sub).where(sub.c.capture_rank<=limit).order_by(sub.c.symbol,sub.c.snapshot_at.desc())).mappings()]

    def observations(self, dataset: str, symbols: list[str] | None = None, as_of: str | datetime | None = None, limit: int = 2) -> dict:
        """Newest complete capture clocks per symbol/frequency; pass observed_at to query."""
        get_dataset(dataset)
        if limit<1 or limit>100: raise ValueError("limit must be 1..100")
        if dataset in ("analyst_estimates","etf_holdings"):
            rows=self._snapshot_rows(dataset,symbols,as_of,limit)
            return serializable({"dataset":dataset,"observations":[{**r,"observed_at":instant(r["snapshot_at"]),"snapshot_at":instant(r["snapshot_at"]),"estimate_period":r["frequency"]} for r in rows]})
        paths=self._paths(dataset)
        if not paths:return {"dataset":dataset,"observations":[]}
        predicates=[];params=[]
        if symbols is not None:
            if not symbols:return {"dataset":dataset,"observations":[]}
            predicates.append("symbol IN (SELECT unnest(?))");params.append(symbols)
        if as_of:
            cut=cutoff_instant(as_of);predicates.append("observed_at<=? AND available_at<=?");params.extend([cut,cut])
        where=" WHERE "+" AND ".join(predicates) if predicates else ""
        with duckdb.connect(":memory:") as db:
            db.from_parquet(paths,union_by_name=True).create_view("facts")
            cursor=db.execute("SELECT symbol,observed_at,batch_id,raw_ref,count(*) AS row_count FROM facts"+where+" GROUP BY symbol,observed_at,batch_id,raw_ref QUALIFY dense_rank() OVER (PARTITION BY symbol ORDER BY observed_at DESC)<=? ORDER BY symbol,observed_at DESC",[*params,limit])
            columns=[r[0] for r in cursor.description]
            rows=[serializable(dict(zip(columns,r))) for r in cursor.fetchall()]
        return {"dataset":dataset,"observations":rows}

    def latest(self, dataset: str, symbols: list[str] | None = None, as_of: str | datetime | None = None, limit: int = 1000) -> dict:
        spec=get_dataset(dataset)
        if as_of is not None and spec.current_keys and dataset != "analyst_estimates" and (
            spec.date is None or spec.date in spec.keys
        ):
            if limit < 1 or limit > 100000:
                raise ValueError("limit must be 1..100000")
            cutoff = cutoff_instant(as_of)
            visible = self._visible_current(dataset, symbols, cutoff, limit)
            if visible is not None:
                return visible
        if as_of is not None or not spec.current_keys or dataset=="analyst_estimates":
            return self.query(dataset,symbols=symbols,as_of=as_of,limit=limit,_latest=True)
        query=select(current.c.payload).where(current.c.dataset==dataset).order_by(current.c.symbol,current.c.key).limit(limit)
        if symbols is not None: query=query.where(current.c.symbol.in_(symbols))
        with self.engine.connect() as conn:
            rows=[r[0] for r in conn.execute(query)]
            count_query=select(func.count()).select_from(current).where(current.c.dataset==dataset)
            if symbols is not None:count_query=count_query.where(current.c.symbol.in_(symbols))
            total=conn.execute(count_query).scalar_one()
        return {"dataset":dataset,"rows":rows,"total":total,"limit":limit,"offset":0,"provenance":{"as_of":None,"version_policy":"current_projection","historical_use":spec.historical_use}}

    def _visible_current(self, dataset, symbols, cutoff, limit):
        """Use the current winners only when every winner was visible at cutoff.

        For immutable fact dates (or dates derived from the observation clock),
        the projection and historical latest query have the same ranking. If all
        projected winners are visible, no historical row can replace them. A
        future or unknown availability clock requires the historical query.
        Window aggregates examine the entire scope before LIMIT, in the same
        database snapshot as the returned rows.
        """
        # Ingest/import retain normalized UTC ISO clocks. Pad absent fractional
        # seconds for exact microsecond comparison on PostgreSQL and SQLite;
        # SQLite's julianday would round away meaningful submillisecond clocks.
        available = current.c.payload["available_at"].as_string()
        available_key = func.substr(func.replace(available, "+00:00", "") + ".000000", 1, 26)
        cutoff_key = cutoff.replace(tzinfo=None).isoformat(timespec="microseconds")
        known = and_(current.c.observed_at <= cutoff, available_key <= cutoff_key)
        query = select(current.c.payload,
            func.count().over().label("total"),
            func.max(case((known, 0), else_=1)).over().label("requires_history"),
        ).where(current.c.dataset == dataset)
        if symbols is not None:
            query = query.where(current.c.symbol.in_(symbols))
        query = query.order_by(current.c.payload["date"].as_string().desc().nulls_last(),
            current.c.symbol, current.c.observed_at.desc(), current.c.batch_id, current.c.row_index).limit(limit)
        with self.engine.connect() as conn:
            page = conn.execute(query).all()
        if page and page[0].requires_history:
            return None
        return {"dataset": dataset, "rows": [row.payload for row in page],
            "total": page[0].total if page else 0, "limit": limit, "offset": 0,
            "provenance": {"as_of": serializable(cutoff), "version_policy": "latest_observed_per_fact",
                "historical_use": get_dataset(dataset).historical_use}}

    def read_source(self, source_id: str) -> dict:
        parts=source_id.split(":")
        if len(parts)!=3 or parts[0]!="numeric": raise ValueError("Invalid numeric source_id")
        batch_id,index=parts[1],int(parts[2])
        query=select(files.c.path,batches.c.dataset).join(batches,batches.c.id==files.c.batch_id).where(batches.c.id==batch_id,batches.c.status=="ready",files.c.first_row<=index,files.c.last_row>=index)
        with self.engine.connect() as conn: row=conn.execute(query).first()
        if row is None: raise KeyError(source_id)
        with duckdb.connect(":memory:") as db:
            cursor=db.execute("SELECT * FROM read_parquet(?) WHERE row_index=?",[str(self.settings.data_root/row.path),index])
            names=[c[0] for c in cursor.description]
            record=cursor.fetchone()
        if record is None: raise KeyError(source_id)
        return serializable({"dataset":row.dataset,**dict(zip(names,record))})
