"""One-time import of explicitly selected source evidence; no runtime source dependency."""
from __future__ import annotations

import gzip
import hashlib
import json
import shutil
from collections.abc import Callable
from pathlib import Path
from datetime import datetime, timezone

import duckdb
import pyarrow as pa
from sqlalchemy import select
from .schema import batches

from studio_market.config import MarketSettings
from .datasets import DATASETS, dataset
from .raw import archive_response
from .store import NumericStore
from .providers.analyst import normalize_analyst_estimates, normalize_price_targets, normalize_rating_consensus

ANALYST_REPLAY={
    "analyst_estimates":("analyst-estimates-bulk",normalize_analyst_estimates),
    "analyst_price_targets":("price-target-summary-bulk",normalize_price_targets),
    "analyst_rating_consensus":("upgrades-downgrades-consensus-bulk",normalize_rating_consensus),
}


def migration_plan(source_root: str | Path, names: list[str] | None = None) -> dict:
    root=Path(source_root).expanduser().resolve()
    source=root/"market_research.duckdb"
    if not source.is_file(): raise ValueError("Source root must contain market_research.duckdb")
    with duckdb.connect(str(source),read_only=True) as db:
        available={r[0] for r in db.execute("SELECT table_name FROM information_schema.tables").fetchall()}
    derived={"financial_facts":{"financial_statement_observations","financial_fact_observations"},"as_reported_facts":{"as_reported_statement_observations","as_reported_fact_observations"}}
    selected=names or [name for name,spec in DATASETS.items() if spec.source_table in available or (name in derived and derived[name]<=available)]
    plan=[]
    for name in selected:
        spec=dataset(name)
        if "minute" in name: raise ValueError("Minute data is outside this data layer")
        if spec.source_table is None and name not in ("financial_facts","as_reported_facts"):
            raise ValueError(f"No source migration for {name}")
        if spec.source_table and spec.source_table not in available:
            raise ValueError(f"Source lacks {spec.source_table}")
        plan.append({"dataset":name,"source_table":spec.source_table,"mode":"raw_capture_replay" if name in ANALYST_REPLAY and "raw_captures" in available else "version_rows"})
    return {"source_root":str(root),"source_database":str(source),"datasets":plan,"excluded":"All minute archives, minute facts, minute-derived materializations and source Parquet exports"}


def _source_query(name: str) -> str:
    spec=dataset(name)
    if name == "financial_facts":
        return """SELECT s.*, f.line_item, f.value FROM financial_statement_observations s
          JOIN financial_fact_observations f USING(statement_content_sha256)
          WHERE s.period_end<=CAST(s.collected_at AS DATE) AND abs(year(s.period_end)-s.fiscal_year)<=1"""
    if name == "as_reported_facts":
        return """SELECT s.*,f.concept,f.value_text,f.numeric_value FROM as_reported_statement_observations s
          JOIN as_reported_fact_observations f USING(statement_content_sha256)
          WHERE s.period_end<=CAST(s.collected_at AS DATE) AND abs(year(s.period_end)-s.fiscal_year)<=1"""
    query=f'SELECT * FROM "{spec.source_table}"'
    if name in ("financial_statements","as_reported_statements"):
        query+=" WHERE period_end<=CAST(collected_at AS DATE) AND abs(year(period_end)-fiscal_year)<=1"
    if name=="cn_futures_observations":
        query+=" WHERE dataset NOT LIKE '%minute%' AND dataset NOT LIKE '%viewpoint%' AND dataset NOT LIKE '%notice%' AND dataset NOT LIKE '%reminder%'"
    return query


def migrate_source(settings: MarketSettings, source_root: str | Path, *, names: list[str] | None=None, batch_rows: int=65536, reimport: bool=False, progress: Callable[[dict],None] | None=None) -> dict:
    """Copy referenced raw objects and stream original observations into new Parquet.

    Does not import source Parquet/current views and never opens source for writing.
    Original observed clocks are preserved; import time is recorded in batch metadata.
    """
    if batch_rows<1: raise ValueError("batch_rows must be positive")
    plan=migration_plan(source_root,names)
    root=Path(plan["source_root"])
    if settings.data_root.resolve()==root or root in settings.data_root.resolve().parents:
        raise ValueError("Target must be independent of source root")
    store=NumericStore(settings)
    results=[]
    raw_map={}
    raw_destinations={}
    with duckdb.connect(plan["source_database"],read_only=True) as db:
        tables={r[0] for r in db.execute("SELECT table_name FROM information_schema.tables").fetchall()}
        if "raw_objects" in tables:
            raw_map={sha:path for sha,path in db.execute("SELECT raw_sha256,relative_path FROM raw_objects WHERE relative_path NOT LIKE '%minute%' ORDER BY retrieved_at").fetchall()}
        def copy_raw(sha):
            if not sha: return None
            if sha in raw_destinations: return raw_destinations[sha]
            path=raw_map.get(sha)
            if not path: raise ValueError("Source observation has no raw object record")
            original=(root/path).resolve()
            if root not in original.parents or "minute" in str(original.relative_to(root)):
                raise ValueError("Raw source path escapes the selected numeric source")
            relative=Path("numeric/raw/imported")/sha[:2]/f"{sha}.gz"
            target=settings.data_root/relative
            target.parent.mkdir(parents=True,exist_ok=True)
            if not target.exists():
                temporary=target.with_suffix(".copying")
                shutil.copyfile(original,temporary)
                identity=hashlib.sha256()
                with gzip.open(temporary,"rb") as decoded:
                    while block:=decoded.read(1024*1024):identity.update(block)
                if identity.hexdigest()!=sha:
                    temporary.unlink()
                    raise ValueError("Source raw object does not match its recorded content identity")
                temporary.replace(target)
            raw_destinations[sha]=str(relative)
            return str(relative)
        allowed=set()
        if "us_security_directory" in tables:
            allowed={r[0] for r in db.execute("SELECT symbol FROM us_security_directory WHERE NOT coalesce(is_etf,false) AND NOT coalesce(is_fund,false)").fetchall()}
        for source_table in ("analyst_estimate_observations","analyst_price_target_observations","analyst_rating_consensus_observations"):
            if source_table in tables:allowed.update(r[0] for r in db.execute(f"SELECT DISTINCT symbol FROM {source_table}").fetchall())
        for item in plan["datasets"]:
            name=item["dataset"]
            with store.engine.connect() as conn:
                prior=conn.execute(select(batches.c.id,batches.c.details,batches.c.row_count).where(batches.c.dataset==name,batches.c.source=="imported_market_archive",batches.c.status=="ready").order_by(batches.c.published_at.desc())).all()
            completed=next((r for r in prior if r.details.get("migration_source")==str(root)),None)
            if completed and not reimport:
                results.append({"dataset":name,"batch_id":completed.id,"status":"already_imported","row_count":completed.row_count})
                continue
            source_sql=_source_query(name)
            unfiltered=source_sql.split(" WHERE ")[0] if " WHERE " in source_sql else source_sql.split("\n          WHERE")[0]
            source_count=db.execute("SELECT count(*) FROM ("+unfiltered+")").fetchone()[0]
            included_count=db.execute("SELECT count(*) FROM ("+source_sql+")").fetchone()[0]
            exclusions={"source_rows":source_count,"eligible_source_rows":included_count,"excluded_rows":source_count-included_count,"reason":"future_period_or_fiscal_year_mismatch" if name in ("financial_statements","financial_facts","as_reported_statements","as_reported_facts") else "text_or_minute_data_out_of_scope" if name=="cn_futures_observations" else None}
            if exclusions["excluded_rows"] and name in ("financial_statements","financial_facts","as_reported_statements","as_reported_facts"):
                for sha, in db.execute("SELECT DISTINCT raw_sha256 FROM ("+unfiltered+")").fetchall():copy_raw(sha)
            scopes=[]
            if progress: progress({"dataset":name,"status":"importing"})
            if item["mode"]=="raw_capture_replay":
                endpoint,normalizer=ANALYST_REPLAY[name]
                captures=db.execute("SELECT raw_sha256,relative_path,retrieved_at,parameters_json FROM raw_captures WHERE endpoint=? ORDER BY retrieved_at,capture_id",[endpoint]).fetchall()
                capture_groups=[]
                if name=="analyst_estimates":
                    pending={}
                    for cap in captures:
                        params=json.loads(cap[3]);period=params["period"];year=int(params["year"])
                        group=pending.setdefault(period,{})
                        if year in group:
                            capture_groups.append((period,list(group.values())));group={};pending[period]=group
                        group[year]=cap
                    capture_groups.extend((p,list(g.values())) for p,g in pending.items())
                else:capture_groups=[("",[cap]) for cap in captures]
                def replay():
                    for period,group in capture_groups:
                        complete=name!="analyst_estimates" or len(group)>=(5 if period=="annual" else 3)
                        snapshot_at=max(cap[2] for cap in group)
                        if name=="analyst_estimates" and complete:
                            scopes.extend({"symbol":symbol,"frequency":period,"snapshot_at":snapshot_at.isoformat()} for symbol in allowed)
                        for sha,path,observed,params_json in group:
                            # Retain each capture, including repeated content A -> B -> A.
                            ref=copy_raw(sha)
                            body=gzip.decompress((settings.data_root/ref).read_bytes())
                            options=dict(allowed_symbols=allowed,raw_sha256=sha,collected_at=observed)
                            if name=="analyst_estimates":options["estimate_period"]=period
                            rows=normalizer(body,**options)
                            for row in rows:row.update(raw_ref=ref,snapshot_at=snapshot_at,snapshot_complete=complete)
                            for offset in range(0,len(rows),batch_rows):yield rows[offset:offset+batch_rows]
                chunks=replay()
            else:
                reader=db.execute(_source_query(name)).fetch_record_batch(batch_rows)
                def source_chunks():
                    for batch in reader:
                        # Bounded conversion keeps source schema and each original revision.
                        rows=pa.Table.from_batches([batch]).to_pylist()
                        for row in rows:
                            row["raw_ref"]=copy_raw(row.get("raw_sha256"))
                            if "collected_at" not in row:
                                row["historical_use"]="catalog_imported_at_migration"
                        yield rows
                chunks=source_chunks()
            result=store.ingest(name,chunks,source="imported_market_archive",details={"migration_source":str(root),"migration_mode":item["mode"],"source_table":item["source_table"],"source_parquet_used":False,"source_counts":exclusions,"snapshot_scopes":scopes})
            result["source_counts"]=exclusions
            results.append(result)
            if progress:progress(result)
    return {"batches":results,"raw_objects_copied":len(raw_destinations),"excluded":plan["excluded"]}
