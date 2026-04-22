from __future__ import annotations

from uuid import uuid4


def make_recalc_job_id() -> str:
    return f"job_{uuid4().hex[:12]}"
