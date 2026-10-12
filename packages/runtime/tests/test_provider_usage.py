import json
from pathlib import Path

import pytest
from studio_runtime.provider_usage import (BUCKETS, MAX_USAGE_BYTES, capture_provider_usage,
    merge_provider_usage, parse_provider_usage)


def wire(*rows):
    return b"\n".join(json.dumps(row).encode() for row in rows)


def row(kind, step=0, invocation="gen", **extra):
    return dict(invocation_id=invocation, session_id="session-one", turn=0, step=step, kind=kind, **extra)


def tokens(n):
    return dict(zip(BUCKETS, (n, 7, 3, 0)))


def test_replace_same_step_then_add_distinct_steps_and_stages():
    data = wire(row("start"), row("usage", tokens=tokens(8)), row("usage", tokens=tokens(10)), row("end"),
        row("start", 1), row("usage", 1, tokens=tokens(20)), row("end", 1), row("turn_end"),
        row("start", invocation="review"), row("usage", invocation="review", tokens=tokens(5)),
        row("end", invocation="review"), row("turn_end", invocation="review"))
    result = parse_provider_usage(data, {"gen": "generation", "review": "review"})["invocations"]
    assert [item["status"] for item in result] == ["complete", "complete"]
    assert result[0]["reported_calls"] == 2 and result[0]["tokens"]["uncached_input_tokens"] == 30
    assert result[0]["tokens"]["output_tokens"] == 14
    assert result[1]["stage"] == "review" and result[1]["tokens"]["uncached_input_tokens"] == 5


@pytest.mark.parametrize("tail", [row("start", 1), {"kind": "overflow"}, {"secret": "NEVER_ECHO"}])
def test_interruption_and_invalid_or_full_file_never_claim_complete(tail):
    result = parse_provider_usage(wire(row("start"), row("usage", tokens=tokens(5)), row("end"),
        row("turn_end"), tail), {"gen": "generation"})
    assert result["invocations"][0]["status"] == "partial"
    assert "NEVER_ECHO" not in json.dumps(result)


def test_absent_usage_and_adapter_zero_error_placeholder_are_unknown_not_free():
    for data in (b"", wire(row("start"), row("usage", tokens={key: 0 for key in BUCKETS}), row("end"), row("turn_end"))):
        item = parse_provider_usage(data, {"gen": "generation"})["invocations"][0]
        assert item["status"] == "unknown" and item["tokens"] is None
    value = parse_provider_usage(b" " * (MAX_USAGE_BYTES + 1), {"gen": "generation"})
    assert value["invocations"][0]["status"] == "unknown"


def test_capture_private_cleanup_on_exception_and_idempotent_persistence(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path))
    saved = []
    with pytest.raises(RuntimeError):
        with capture_provider_usage(saved.append, run_id="offline") as capture:
            env = capture.environment("generation")
            path = Path(env["INVESTMENT_STUDIO_MODEL_USAGE_FILE"])
            assert path.stat().st_mode & 0o777 == 0o600
            assert path.parent.stat().st_mode & 0o777 == 0o700
            assert Path(env["INVESTMENT_STUDIO_MODEL_USAGE_PATCH"]).stat().st_mode & 0o777 == 0o600
            raise RuntimeError("test only")
    assert not path.exists()
    assert saved[0]["invocations"][0]["status"] == "unknown"
    assert merge_provider_usage(saved[0], saved[0]) == saved[0]
