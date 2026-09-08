"""Pull one immutable bundle over authenticated SSH, then import it locally."""

from __future__ import annotations

from pathlib import Path, PurePosixPath
import re
import subprocess
import tempfile

from .store import TextStore


def pull_bundle(store: TextStore, *, host: str, remote_path: str, timeout: int = 3600) -> dict:
    """The SSH host must already be trusted in the user's SSH configuration."""
    if not re.fullmatch(r"[A-Za-z0-9_.@-]+", host) or host.startswith("-"):
        raise ValueError("Use a configured SSH host alias or user@hostname")
    if not remote_path.startswith("/") or not re.fullmatch(r"[A-Za-z0-9_./-]+", remote_path) or ".." in PurePosixPath(remote_path).parts:
        raise ValueError("Use an absolute remote bundle path without shell syntax")
    staging = store.data_root / "incoming"
    staging.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="delivery-", dir=staging) as temporary:
        path = Path(temporary) / PurePosixPath(remote_path).name
        receipt = Path(str(path) + ".sha256")
        options = ["scp", "-q", "-o", "BatchMode=yes", "-o", "StrictHostKeyChecking=yes", "-o", "ConnectTimeout=10", "--"]
        # Both the expected archive digest and archive arrive from the same pinned
        # SSH identity. Never trust an unauthenticated HTTP checksum as provenance.
        subprocess.run([*options, host + ":" + remote_path + ".sha256", str(receipt)], check=True, capture_output=True, timeout=min(timeout, 60))
        fields = receipt.read_text(encoding="utf-8").strip().split()
        if len(fields) != 2 or not re.fullmatch(r"[a-f0-9]{64}", fields[0]) or fields[1] != path.name:
            raise ValueError("Invalid authenticated bundle checksum receipt")
        subprocess.run([*options, host + ":" + remote_path, str(path)], check=True, capture_output=True, timeout=timeout)
        return store.import_bundle(path, expected_sha256=fields[0])
