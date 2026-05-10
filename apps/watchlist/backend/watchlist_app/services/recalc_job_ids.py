from __future__ import annotations

import json
from uuid import uuid4


def make_recalc_job_id() -> str:
    return f"job_{uuid4().hex[:12]}"


def make_recalc_dedupe_key(
    *,
    job_type: str,
    instrument_id: str,
    trigger_type: str,
    trigger_ref_type: str | None,
    trigger_ref_id: str | None,
) -> str:
    return json.dumps(
        {
            "instrument_id": instrument_id,
            "job_type": job_type,
            "trigger_ref_id": trigger_ref_id,
            "trigger_ref_type": trigger_ref_type,
            "trigger_type": trigger_type,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
