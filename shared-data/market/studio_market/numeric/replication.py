"""One numeric bundle protocol for the two Studio installations."""
from __future__ import annotations

import gzip
import hashlib
import json
import os
import re
import tempfile
import zipfile
import zlib
from datetime import datetime,timezone
from pathlib import Path,PurePosixPath

import duckdb
import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq
from sqlalchemy import insert,select,update,text

from studio_market.config import MarketSettings
from .datasets import dataset as get_dataset
from .schema import batches,current,datasets,files,snapshots
from .store import NumericStore,instant,cutoff_instant,serializable

FORMAT="investment-studio-numeric-bundle"
VERSION=1


def _stream_digest(stream):
    result=hashlib.sha256()
    while chunk:=stream.read(1024*1024):result.update(chunk)
    return result.hexdigest()


def digest(path):
    with Path(path).open("rb") as stream:
        return _stream_digest(stream)


def relative_path(value):
    path=PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or not path.parts or path.parts[0]!="numeric":
        raise ValueError("Bundle contains a path outside numeric storage")
    return Path(*path.parts)


def _existing_object_matches(path, item, archive):
    if path.stat().st_size == item["bytes"] and digest(path) == item["sha256"]:
        return True
    # Only raw responses use the decoded body as their path identity. Historical
    # gzip writers on Linux/macOS can encode the same body differently. Parquet
    # and every other immutable object still require exact bytes.
    match = re.fullmatch(r"numeric/raw/[^/]+/([0-9a-f]{2})/([0-9a-f]{64})\.gz", item["path"])
    if match is None or match[1] != match[2][:2]:
        return False
    with archive.open(item["path"]) as incoming:
        if _stream_digest(incoming) != item["sha256"]:
            raise ValueError("Numeric bundle object integrity mismatch")
    try:
        with gzip.open(path, "rb") as existing:
            if _stream_digest(existing) != match[2]:
                return False
        with archive.open(item["path"]) as incoming, gzip.GzipFile(fileobj=incoming, mode="rb") as decoded:
            return _stream_digest(decoded) == match[2]
    except (gzip.BadGzipFile, EOFError, zlib.error):
        return False


def export_bundle(settings: MarketSettings, output: str|Path, *, batch_ids: list[str]|None=None,since: str|datetime|None=None,names: list[str]|None=None)->dict:
    store=NumericStore(settings)
    try:return _export_bundle(store,output,batch_ids=batch_ids,since=since,names=names)
    finally:store.close()


def _export_bundle(store,output,*,batch_ids,since,names):
    settings=store.settings
    query=select(batches).where(batches.c.status=="ready").order_by(batches.c.published_at,batches.c.id)
    if batch_ids is not None:query=query.where(batches.c.id.in_(batch_ids))
    if names is not None:
        for name in names:get_dataset(name)
        query=query.where(batches.c.dataset.in_(names))
    if since:query=query.where(batches.c.published_at>cutoff_instant(since))
    with store.engine.connect() as conn:
        selected=[dict(row) for row in conn.execute(query).mappings()]
        ids=[r["id"] for r in selected]
        parts=[dict(r) for r in conn.execute(select(files).where(files.c.batch_id.in_(ids))).mappings()]
        scopes=[dict(r) for r in conn.execute(select(snapshots).where(snapshots.c.batch_id.in_(ids))).mappings()]
    objects={}
    def add(relative):
        if not relative:return
        path=settings.data_root/relative_path(relative)
        objects[relative]={"path":relative,"bytes":path.stat().st_size,"sha256":digest(path)}
    for part in parts:
        add(part["path"])
        parquet=pq.ParquetFile(settings.data_root/part["path"])
        refs=[name for name in parquet.schema_arrow.names if name.endswith("raw_ref")]
        for chunk in parquet.iter_batches(batch_size=65536,columns=refs):
            for column in chunk.columns:
                for ref in column.unique().to_pylist():
                    if ref and ref not in objects:add(ref)
    # Discovery/calendar receipts are also provenance and must travel with facts.
    for batch in selected:
        for key,value in batch["details"].items():
            if key.endswith("raw_ref") and isinstance(value,str):add(value)
    manifest=serializable({"format":FORMAT,"version":VERSION,"created_at":datetime.now(timezone.utc),"batches":selected,"files":parts,"snapshots":scopes,"objects":list(objects.values())})
    target=Path(output).expanduser();target.parent.mkdir(parents=True,exist_ok=True)
    temporary=target.with_name(target.name+".tmp")
    with zipfile.ZipFile(temporary,"w",compression=zipfile.ZIP_STORED,allowZip64=True) as archive:
        archive.writestr("manifest.json",json.dumps(manifest,ensure_ascii=False,separators=(",",":")))
        for item in objects.values():archive.write(settings.data_root/item["path"],item["path"])
    os.replace(temporary,target)
    return {"path":str(target.resolve()),"batch_count":len(selected),"object_count":len(objects),"last_published_at":manifest["batches"][-1]["published_at"] if selected else None,"format":FORMAT,"version":VERSION}


def import_bundle(settings: MarketSettings, source: str|Path)->dict:
    store=NumericStore(settings)
    try:return _import_bundle(store,source)
    finally:store.close()


def _already_imported(conn,incoming,parts):
    batch_id=incoming["id"]
    existing=conn.execute(select(batches).where(batches.c.id==batch_id)).mappings().first()
    if existing is None:return False
    if existing["status"]!="ready" or existing["dataset"]!=incoming["dataset"] or existing["row_count"]!=incoming["row_count"]:
        raise ValueError("Numeric batch identity is already used by a different publication")
    stored=[dict(r) for r in conn.execute(select(files).where(files.c.batch_id==batch_id)).mappings()]
    if sorted(stored,key=lambda r:r["part"])!=sorted(parts,key=lambda r:r["part"]):
        raise ValueError("Numeric batch identity conflicts with existing catalog")
    return True


def _import_bundle(store,source):
    settings=store.settings;receipts=[]
    with zipfile.ZipFile(source) as archive, duckdb.connect() as analysis:
        manifest=json.loads(archive.read("manifest.json"))
        if manifest.get("format")!=FORMAT or manifest.get("version")!=VERSION:raise ValueError("Unsupported numeric bundle protocol")
        declared={r["path"]:r for r in manifest["objects"]}
        members=archive.namelist()
        if len(members)!=len(set(members)) or set(members)!={"manifest.json",*declared}:raise ValueError("Numeric bundle members differ from manifest")
        for relative,item in declared.items():
            path=settings.data_root/relative_path(relative)
            if archive.getinfo(relative).file_size!=item["bytes"]:raise ValueError("Numeric bundle object size mismatch")
            path.parent.mkdir(parents=True,exist_ok=True)
            if path.exists():
                if not _existing_object_matches(path, item, archive):
                    raise ValueError("Numeric bundle conflicts with an existing immutable object")
                continue
            descriptor, temporary_name = tempfile.mkstemp(prefix=".receiving-", dir=path.parent)
            temporary = Path(temporary_name)
            try:
                h=hashlib.sha256()
                with os.fdopen(descriptor, "wb") as output, archive.open(relative) as incoming:
                    while chunk:=incoming.read(1024*1024):h.update(chunk);output.write(chunk)
                    output.flush()
                    if h.hexdigest()!=item["sha256"]:
                        raise ValueError("Numeric bundle object integrity mismatch")
                    # Preserve inherited named-reader ACLs across atomic linking;
                    # the object's other writers need read access, not write.
                    os.fchmod(output.fileno(), 0o640)
                    os.fsync(output.fileno())
                # A concurrent importer must never overwrite an immutable object
                # or share our partially written staging file.
                try:
                    os.link(temporary, path)
                except FileExistsError:
                    if not _existing_object_matches(path, item, archive):
                        raise ValueError("Numeric bundle conflicts with an existing immutable object")
            finally:
                temporary.unlink(missing_ok=True)
        for incoming in manifest["batches"]:
            name=incoming["dataset"];get_dataset(name);batch_id=incoming["id"]
            if incoming["status"]!="ready":raise ValueError("Only published numerical batches may be imported")
            parts=[r for r in manifest["files"] if r["batch_id"]==batch_id]
            scopes=[r for r in manifest.get("snapshots",[]) if r["batch_id"]==batch_id]
            for part in parts:
                if part["path"] not in declared:raise ValueError("Numeric batch references an undeclared Parquet file")
                if pq.ParquetFile(settings.data_root/part["path"]).metadata.num_rows!=part["row_count"]:raise ValueError("Numerical Parquet row count mismatch")
            if sum(p["row_count"] for p in parts)!=incoming["row_count"]:raise ValueError("Numeric batch row count mismatch")
            with store.engine.connect() as conn:
                if _already_imported(conn,incoming,parts):
                    receipts.append({"batch_id":batch_id,"status":"already_imported"});continue
            projections={}
            spec=get_dataset(name)
            for part in parts:
                path=settings.data_root/part["path"]
                for chunk in pq.ParquetFile(path).iter_batches(batch_size=65536,columns=["batch_id"]):
                    if not pc.all(pc.fill_null(pc.equal(chunk.column(0),batch_id),False)).as_py():
                        raise ValueError("Parquet batch identity mismatch")
                if spec.current_keys:
                    # Select the same per-key winners as _projection_rank without
                    # decoding tens of millions of historical rows into Python.
                    indices=analysis.execute("""SELECT row_index FROM read_parquet(?)
                        QUALIFY row_number() OVER (PARTITION BY _current_key
                            ORDER BY date DESC NULLS LAST, observed_at DESC, row_index DESC)=1""",
                        [str(path)]).fetchall()
                    # Read winners from Arrow to retain the original payload types
                    # and timestamp zones, independent of DuckDB's session zone.
                    table=pq.read_table(path)
                    winners=table.filter(pc.is_in(table["row_index"],value_set=pa.array([row[0] for row in indices]))).to_pylist()
                    for row in winners:
                        key=row["_current_key"]
                        candidate={"key":key,"symbol":row["symbol"],"observed_at":instant(row["observed_at"]),"batch_id":batch_id,"row_index":row["row_index"],"payload":row}
                        if key not in projections or store._projection_rank(candidate)>=store._projection_rank(projections[key]):projections[key]=candidate
            with store.engine.begin() as conn:
                if conn.dialect.name=="postgresql":conn.execute(text("SELECT pg_advisory_xact_lock(hashtext(:name))"),{"name":"studio_market:"+name})
                # Another importer may have published while this process read Parquet.
                if _already_imported(conn,incoming,parts):
                    receipts.append({"batch_id":batch_id,"status":"already_imported"});continue
                store._register_dataset(conn, spec)
                values=dict(incoming)
                for field in ("started_at","published_at"):values[field]=instant(values[field]) if values[field] else None
                conn.execute(insert(batches).values(**values))
                if parts:conn.execute(insert(files),parts)
                if scopes:
                    for scope in scopes:scope["snapshot_at"]=instant(scope["snapshot_at"])
                    conn.execute(insert(snapshots),scopes)
                store._publish_current(conn,name,(dict(row,payload=serializable(row["payload"])) for row in projections.values()),scope_records=scopes)
                conn.execute(update(datasets).where(datasets.c.name==name,
                    (datasets.c.updated_at.is_(None)) | (datasets.c.updated_at<values["published_at"]))
                    .values(updated_at=values["published_at"]))
                receipts.append({"batch_id":batch_id,"status":"ready","row_count":incoming["row_count"]})
    return {"status":"ready","batches":receipts,"format":FORMAT,"version":VERSION}
