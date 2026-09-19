from __future__ import annotations

import gzip
import hashlib
import os
import tempfile
from pathlib import Path

from studio_market.config import MarketSettings


def archive_response(settings: MarketSettings, provider: str, body: bytes) -> tuple[str, str]:
    digest=hashlib.sha256(body).hexdigest()
    relative=Path("numeric/raw")/provider/digest[:2]/f"{digest}.gz"
    target=settings.data_root/relative
    target.parent.mkdir(parents=True,exist_ok=True)
    if not target.exists():
        descriptor, temporary = tempfile.mkstemp(prefix=".capture-", dir=target.parent)
        try:
            with os.fdopen(descriptor,"wb") as output:
                compressed = bytearray(gzip.compress(body,compresslevel=6,mtime=0))
                # Python's mtime=0 fast path can inherit zlib's host OS byte.
                # Raw identity is the response body, so keep this header portable.
                compressed[9] = 255
                output.write(compressed)
                output.flush()
                # mkstemp's 0600 masks inherited named-reader ACL entries.
                # Enable their read bit only on this new public object.
                os.fchmod(output.fileno(), 0o640)
                os.fsync(output.fileno())
            os.replace(temporary,target)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return digest,str(relative)
