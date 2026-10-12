import os
from pathlib import Path
import sys

os.environ.setdefault("INVESTMENT_STUDIO_BRIEFING_DATABASE_URL", "sqlite+pysqlite:///:memory:")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


import pytest


@pytest.fixture(autouse=True)
def isolated_model_work_control(tmp_path, monkeypatch):
    # Tests use fake Harness processes. Never read or change the host switch.
    path = tmp_path / "model-work.json"
    path.write_text('{"paused": false}', encoding="utf-8")
    monkeypatch.setenv("INVESTMENT_STUDIO_MODEL_WORK_CONTROL_FILE", str(path))
