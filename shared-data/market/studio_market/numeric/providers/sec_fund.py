from __future__ import annotations
from dataclasses import dataclass
from datetime import date,datetime,timezone
from typing import Any
from urllib.parse import quote

@dataclass(frozen=True)
class SecFundFiling:
    cik: str
    filing_date: date
    report_date: date
    accepted_at: datetime
    form_type: str
    accession_number: str
    primary_document: str

    @property
    def document_url(self) -> str:
        accession = self.accession_number.replace("-", "")
        document = quote(self.primary_document, safe="/._-")
        return (
            "https://www.sec.gov/Archives/edgar/data/"
            f"{int(self.cik)}/{accession}/{document}"
        )

def _date_at(batch: dict[str, Any], key: str, index: int) -> date | None:
    value = _text_at(batch, key, index)
    if value is None:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None

def _datetime_at(batch: dict[str, Any], key: str, index: int) -> datetime | None:
    value = _text_at(batch, key, index)
    if value is None:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)

def _text_at(batch: dict[str, Any], key: str, index: int) -> str | None:
    values = batch.get(key, [])
    if index >= len(values):
        return None
    value = str(values[index] or "").strip()
    return value or None
