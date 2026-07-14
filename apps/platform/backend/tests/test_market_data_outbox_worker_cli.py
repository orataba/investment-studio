from __future__ import annotations

import importlib.util
from pathlib import Path


SCRIPT_PATH = (
    Path(__file__).resolve().parents[1] / "scripts" / "run_market_data_outbox_worker.py"
)
SPEC = importlib.util.spec_from_file_location(
    "run_market_data_outbox_worker",
    SCRIPT_PATH,
)
assert SPEC is not None and SPEC.loader is not None
outbox_worker_cli = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(outbox_worker_cli)


def test_stable_worker_prefix_always_gets_unique_process_instance_suffix() -> None:
    first = outbox_worker_cli._worker_id(
        "platform-outbox",
        instance_id="a" * 32,
        hostname="host-a",
        process_id=100,
    )
    second = outbox_worker_cli._worker_id(
        "platform-outbox",
        instance_id="b" * 32,
        hostname="host-a",
        process_id=100,
    )

    assert first == f"platform-outbox:{'a' * 12}"
    assert second == f"platform-outbox:{'b' * 12}"
    assert first != second


def test_default_worker_prefix_includes_host_and_process() -> None:
    worker_id = outbox_worker_cli._worker_id(
        instance_id="c" * 32,
        hostname="host-a",
        process_id=123,
    )

    assert worker_id == f"host-a:123:{'c' * 12}"
