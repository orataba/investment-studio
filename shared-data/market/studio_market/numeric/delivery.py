"""Authenticated directory catch-up for immutable Studio and MI bundles."""
from __future__ import annotations

import json
import os
import re
import subprocess
import tempfile
import fcntl
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path,PurePosixPath

from .replication import digest,export_bundle,import_bundle
from .schema import batches
from .store import NumericStore,serializable
from sqlalchemy import select

SSH=["-o","BatchMode=yes","-o","StrictHostKeyChecking=yes"]


@contextmanager
def file_lock(path,*,blocking=True):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    with path.open('a') as lock:
        fcntl.flock(lock,fcntl.LOCK_EX|(0 if blocking else fcntl.LOCK_NB))
        yield


def _location(host,remote_dir):
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+",host) or host.startswith("-"):
        raise ValueError("Configure a trusted SSH host alias")
    if not remote_dir.startswith("/") or not re.fullmatch(r"[A-Za-z0-9_./-]+",remote_dir) or ".." in PurePosixPath(remote_dir).parts:
        raise ValueError("Configure an absolute remote directory without shell syntax")
    return remote_dir.rstrip("/")


def _atomic_json(path,payload):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name(f".{path.name}.{os.getpid()}.tmp")
    with temporary.open("w") as output:
        json.dump(serializable(payload),output,ensure_ascii=False,indent=2);output.flush();os.fsync(output.fileno())
    os.replace(temporary,path)


def publish_pending(settings,*,outbox: str|Path|None=None)->dict:
    directory=Path(outbox) if outbox else settings.data_root/"numeric/outbox"
    with file_lock(directory/'.publish.lock'):
        return _publish_pending(settings,directory)


def _publish_pending(settings,directory):
    directory.mkdir(parents=True,exist_ok=True)
    index_file=directory/"index.json"
    index=json.loads(index_file.read_text()) if index_file.exists() else {"format":"investment-studio-numeric-index","version":1,"bundles":[]}
    known={batch_id for item in index["bundles"] for batch_id in item["batch_ids"]}
    store=NumericStore(settings)
    try:
        with store.engine.connect() as conn:
            pending=[r[0] for r in conn.execute(select(batches.c.id).where(batches.c.status=="ready").order_by(batches.c.published_at,batches.c.id)) if r[0] not in known]
    finally:store.close()
    if not pending:return {"status":"no_new_batches","batch_count":0,"outbox":str(directory)}
    # Batch IDs, rather than a date watermark, also include work completed out of order.
    filename=f"numeric-{datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S')}-{pending[-1]}.zip"
    path=directory/filename
    result=export_bundle(settings,path,batch_ids=pending)
    checksum=digest(path)
    receipt=directory/(filename+".sha256");receipt.write_text(f"{checksum}  {filename}\n")
    item={"name":filename,"sha256":checksum,"bytes":path.stat().st_size,"batch_ids":pending,"created_at":datetime.now(timezone.utc).isoformat()}
    index["bundles"].append(item)
    _atomic_json(index_file,index)
    return {"status":"published",**result,"sha256":checksum,"index":str(index_file)}


def _copy(host,remote,destination,timeout):
    subprocess.run(["scp","-q",*SSH,"--",host+":"+remote,str(destination)],check=True,capture_output=True,timeout=timeout)


def _state(settings):
    path=settings.data_root/"delivery-status.json"
    return path,json.loads(path.read_text()) if path.exists() else {"numeric":{},"text":{}}


def pull_numeric_directory(settings,*,host: str,remote_dir: str,timeout: int=3600)->dict:
    remote_dir=_location(host,remote_dir)
    state_path,state=_state(settings);key=host+":"+remote_dir
    seen=state["numeric"].setdefault(key,{})
    incoming=settings.data_root/"numeric/incoming";incoming.mkdir(parents=True,exist_ok=True)
    receipts=[];already_present=[]
    store=NumericStore(settings)
    try:
        with store.engine.connect() as conn:
            ready_ids={r[0] for r in conn.execute(select(batches.c.id).where(batches.c.status=="ready"))}
    finally:store.close()
    with tempfile.TemporaryDirectory(prefix="catchup-",dir=incoming) as temporary:
        index_path=Path(temporary)/"index.json";_copy(host,remote_dir+"/index.json",index_path,min(timeout,60))
        index=json.loads(index_path.read_text())
        if index.get("format")!="investment-studio-numeric-index" or index.get("version")!=1:raise ValueError("Unsupported numeric directory index")
        for item in index["bundles"]:
            name=item["name"]
            if not re.fullmatch(r"[A-Za-z0-9_.-]+\.zip",name):raise ValueError("Invalid numeric bundle filename")
            if not re.fullmatch(r"[a-f0-9]{64}",item["sha256"]):raise ValueError("Invalid numeric bundle checksum")
            if name in seen and seen[name]!=item["sha256"]:
                raise ValueError("Published numeric bundle changed identity")
            missing=sorted(set(item['batch_ids'])-ready_ids)
            if item.get('payload_state')=='retired' and missing:
                # A restored database can lack batches even when an old delivery
                # receipt survives. Retired payloads require an explicit bootstrap.
                return {
                    'status':'failed','error_type':'NumericBootstrapRequired',
                    'error':(
                        f'Retired numeric payload {name} requires {len(missing)} missing ready batches. '
                        'On the collector, run bin/investment-studio market numeric export-bundle '
                        '/absolute/new-bootstrap.zip --batch-ids <comma-separated missing_batch_ids>. '
                        'Transfer and verify the new archive using its own checksum, then run '
                        'bin/investment-studio market numeric import-bundle /absolute/new-bootstrap.zip '
                        'on this replica and rerun sync. Do not reuse the retired archive checksum.'
                    ),
                    'bundle':name,'missing_batch_ids':missing,
                    'new_bundles':len(receipts),'receipts':receipts,'already_present':already_present,
                }
            # Delivery receipts can outlive a restored database. Only the
            # committed ready batches establish that no import is needed.
            if name in seen and not missing:
                continue
            if not missing:
                # The initial source and replica can already own these exact
                # immutable batches. Do not transfer a second full bootstrap.
                seen[name]=item['sha256'];already_present.append(name)
                _atomic_json(state_path,state)
                continue
            path=Path(temporary)/name;_copy(host,remote_dir+"/"+name,path,timeout)
            if path.stat().st_size!=item["bytes"] or digest(path)!=item["sha256"]:raise ValueError("Authenticated numeric archive integrity mismatch")
            result=import_bundle(settings,path);receipts.append({"name":name,**result})
            ready_ids.update(item['batch_ids'])
            seen[name]=item["sha256"]
            _atomic_json(state_path,state)
            path.unlink()
    return {"status":"caught_up","new_bundles":len(receipts),"receipts":receipts,"already_present":already_present}


def pull_text_directory(settings,*,host: str,remote_dir: str)->dict:
    from studio_market.text.store import TextStore
    from studio_market.text.delivery import pull_bundle
    remote_dir=_location(host,remote_dir)
    state_path,state=_state(settings);key=host+":"+remote_dir
    seen=state["text"].setdefault(key,{})
    # The validated directory cannot inject shell syntax into SSH's remote command.
    command=f"find {remote_dir} -maxdepth 1 -type f -name 'mi-text-*.zip' -exec basename {{}} \\;"
    listing=subprocess.run(["ssh",*SSH,"--",host,command],check=True,capture_output=True,text=True,timeout=60)
    names=sorted(name for name in listing.stdout.splitlines() if name)
    receipts=[];store=TextStore(settings)
    try:
        imported=store.imported_bundle_ids()
        for name in names:
            if not re.fullmatch(r"mi-text-[a-f0-9]{64}\.zip",name):raise ValueError("Invalid MI bundle filename")
            if name in seen and Path(name).stem in imported:continue
            result=pull_bundle(store,host=host,remote_path=remote_dir+"/"+name)
            receipts.append({"name":name,**result});seen[name]=datetime.now(timezone.utc).isoformat()
            _atomic_json(state_path,state)
    finally:store.close()
    return {"status":"caught_up","new_bundles":len(receipts),"receipts":receipts}


def import_text_directory(settings, *, directory: str | Path) -> dict:
    """Consume completed archives from the dedicated authenticated SFTP inbox."""
    from studio_market.text.store import TextStore
    directory = Path(directory).expanduser().resolve()
    if not directory.is_dir():
        raise ValueError("Configured MI inbox directory does not exist")
    state_path, state = _state(settings)
    seen = state["text"].setdefault("inbox:" + str(directory), {})
    receipts = []
    store = TextStore(settings)
    try:
        imported = store.imported_bundle_ids()
        for path in sorted(directory.glob("mi-text-*.zip")):
            if not re.fullmatch(r"mi-text-[a-f0-9]{64}\.zip", path.name) or path.is_symlink():
                raise ValueError("Invalid MI inbox archive")
            if path.name in seen and path.stem in imported:
                continue
            receipt = path.with_name(path.name + ".sha256")
            if receipt.is_symlink():
                raise ValueError("Invalid MI inbox receipt")
            fields = receipt.read_text().strip().split()
            if len(fields) != 2 or not re.fullmatch(r"[a-f0-9]{64}", fields[0]) or fields[1] != path.name:
                raise ValueError("Invalid MI inbox checksum receipt")
            result = store.import_bundle(path, expected_sha256=fields[0])
            receipts.append({"name": path.name, **result})
            seen[path.name] = fields[0]
            _atomic_json(state_path, state)
    finally:
        store.close()
    return {"status": "caught_up", "new_bundles": len(receipts), "receipts": receipts}
