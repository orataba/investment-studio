"""Canonical storage paths shared by uploads and retained research evidence."""
from pathlib import Path
import re

from watchlist_app.core.settings import get_settings


def safe_file_segment(value: str | None, fallback: str = "file") -> str:
    raw = Path(value or "").name.strip()
    normalized = re.sub(r"[^A-Za-z0-9._-]+", "_", raw).strip("._")
    return normalized or fallback



def instrument_document_dir(instrument_id: str) -> Path:
    return get_settings().document_storage_root / safe_file_segment(instrument_id, fallback="instrument")

