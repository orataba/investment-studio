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
                output.write(gzip.compress(body,compresslevel=6,mtime=0))
                output.flush();os.fsync(output.fileno())
            os.replace(temporary,target)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return digest,str(relative)
