"""Provider-reported usage only; no prompts, heuristic token estimates or costs."""
from contextlib import contextmanager
import json
import logging
import os
from pathlib import Path
import tempfile
from uuid import uuid4

MAX_USAGE_BYTES = 4 * 1024 * 1024
BUCKETS = ("uncached_input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens")


def parse_provider_usage(data: bytes, invocations: dict) -> dict:
    """Fold replaceable samples per actual request, retaining unknown calls.

    Harness emits both a usage chunk and its finalized message for one step.
    Summing both would double charge. A killed call without a usage sample is
    unknown, even if its earlier steps returned known counts.
    """
    incomplete = len(data) > MAX_USAGE_BYTES
    requests, ended = {}, set()
    for line in data[:MAX_USAGE_BYTES].splitlines():
        try:
            row = json.loads(line)
            invocation = row["invocation_id"]
            if invocation not in invocations:
                raise ValueError()
            session = row["session_id"]
            if not isinstance(session, str) or not 1 <= len(session) <= 160:
                raise ValueError()
            turn, step = row["turn"], row.get("step")
            if type(turn) is not int or turn < 0:
                raise ValueError()
            if row["kind"] == "turn_end":
                ended.add((invocation, session, turn))
                continue
            if type(step) is not int or step < 0:
                raise ValueError()
            key = (invocation, session, turn, step)
            state = requests.setdefault(key, {"started": False, "ended": False, "tokens": None})
            if row["kind"] == "start":
                state["started"] = True
            elif row["kind"] == "end":
                state["ended"] = True
            elif row["kind"] == "usage":
                tokens = row["tokens"]
                if set(tokens) != set(BUCKETS) or any(type(tokens[k]) is not int or not 0 <= tokens[k] <= 2**53 - 1 for k in BUCKETS):
                    raise ValueError()
                # The pinned adapter also emits an all-zero placeholder on
                # transport errors with no provider usage. Do not call that free.
                if any(tokens.values()):
                    state["tokens"] = tokens
            else:
                raise ValueError()
        except (KeyError, TypeError, ValueError):
            incomplete = True
    results = []
    for invocation, stage in invocations.items():
        entries = [(key, state) for key, state in requests.items() if key[0] == invocation]
        known = [state for _, state in entries if state["tokens"] is not None]
        complete = bool(entries) and not incomplete and all(state["started"] and state["ended"] and state["tokens"] is not None
            and key[:3] in ended for key, state in entries)
        results.append({"invocation_id": invocation, "stage": stage,
            "status": "complete" if complete else "partial" if known else "unknown",
            "started_calls": sum(state["started"] for _, state in entries), "reported_calls": len(known),
            "unknown_calls": sum(state["tokens"] is None for _, state in entries),
            "tokens": {bucket: sum(state["tokens"][bucket] for state in known) for bucket in BUCKETS} if known else None})
    return {"source": "harness_provider_usage", "invocations": results,
        "note": "Reported token buckets only; interrupted/unreported calls may have additional usage. Reasoning is included in output. No monetary cost is inferred."}


def merge_provider_usage(previous, current):
    entries = {row["invocation_id"]: row for row in (previous or {}).get("invocations", [])}
    entries.update({row["invocation_id"]: row for row in current["invocations"]})
    return {**current, "invocations": list(entries.values())}


class UsageCapture:
    def __init__(self, directory):
        self.path = Path(directory) / "usage.jsonl"
        self.path.touch(mode=0o600)
        self.patch = Path(directory) / "usage.patch.json"
        plugin = Path(__file__).resolve().parents[3] / "infra/harness/provider_usage.mjs"
        self.patch.write_text(json.dumps([{"insert": [{"id": "studio-provider-usage", "name": str(plugin)}]}]))
        self.patch.chmod(0o600)
        self.invocations = {}

    def environment(self, stage):
        if stage not in {"generation", "review", "capture"}:
            raise ValueError("Unknown model usage stage")
        invocation = uuid4().hex
        self.invocations[invocation] = stage
        return {"INVESTMENT_STUDIO_MODEL_USAGE_FILE": str(self.path),
            "INVESTMENT_STUDIO_MODEL_USAGE_PATCH": str(self.patch),
            "INVESTMENT_STUDIO_MODEL_USAGE_INVOCATION": invocation}

    def read(self):
        try:
            with self.path.open("rb") as source:
                data = source.read(MAX_USAGE_BYTES + 1)
        except OSError:
            data = b""  # Lost diagnostic output remains unknown, not zero usage.
        return parse_provider_usage(data, self.invocations)


@contextmanager
def capture_provider_usage(record=None, **identifiers):
    root = Path(os.environ.get("XDG_STATE_HOME", str(Path.home() / ".local/state"))) / "investment-studio/model-usage"
    root.mkdir(parents=True, exist_ok=True, mode=0o700)
    root.chmod(0o700)
    with tempfile.TemporaryDirectory(prefix="call-", dir=root) as directory:
        capture = UsageCapture(directory)
        try:
            yield capture
        finally:
            if capture.invocations:
                from studio_runtime import emit
                summary = capture.read()
                emit("model_provider_usage", **identifiers, **summary)
                if record is not None:
                    try:
                        record(summary)
                    except Exception as error:
                        # Diagnostic persistence cannot replace the execution's
                        # result. Counts remain in the structured operational log.
                        logging.getLogger(__name__).error("Provider usage receipt persistence failed (%s)", type(error).__name__)
