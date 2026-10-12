"""Host-level model execution switch, independent of business permissions.

The non-secret control file is deliberately outside releases. Missing or invalid
configuration pauses work; deploying code never opts a host into paid execution.
"""
import json
import os
from pathlib import Path
import subprocess
import time


class ModelWorkPaused(RuntimeError):
    def __init__(self, message="研究已暂停，已有资料和确定性计算仍可使用。"):
        super().__init__(message)


def control_path() -> Path:
    return Path(os.environ.get("INVESTMENT_STUDIO_MODEL_WORK_CONTROL_FILE",
                              str(Path.home() / ".config/investment-studio/model-work.json")))


def model_work_status() -> dict:
    try:
        # Configuration is tiny; bound reads even if an operator picks a wrong file.
        with control_path().open(encoding="utf-8") as source:
            raw = source.read(4097)
        if len(raw) > 4096:
            raise ValueError("oversized control")
        data = json.loads(raw)
        if not isinstance(data, dict) or type(data.get("paused")) is not bool:
            raise ValueError("invalid control")
    except (OSError, ValueError, UnicodeError):
        return {"paused": True, "code": "model_work_unconfigured", "message": "研究已暂停，等待运行配置就绪。"}
    return {"paused": data["paused"], "code": "model_work_paused" if data["paused"] else None,
            "message": "研究已暂停，已有资料和确定性计算仍可使用。" if data["paused"] else None}


def require_model_work() -> None:
    status = model_work_status()
    if status["paused"]:
        raise ModelWorkPaused(status["message"])


def communicate_model_process(process, *, timeout):
    """Observe operator pauses while draining both pipes; caller owns cleanup."""
    deadline = time.monotonic() + timeout
    while True:
        require_model_work()
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise subprocess.TimeoutExpired(process.args, timeout)
        try:
            return process.communicate(timeout=min(2, remaining))
        except subprocess.TimeoutExpired:
            continue


def main():
    import argparse
    import tempfile
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("status", "pause", "resume"))
    args = parser.parse_args()
    if args.action != "status":
        path = control_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, delete=False) as output:
            temporary = Path(output.name)
            json.dump({"paused": args.action == "pause"}, output)
            output.flush()
            os.fsync(output.fileno())
        try:
            temporary.replace(path)
        finally:
            temporary.unlink(missing_ok=True)
    print(json.dumps(model_work_status(), ensure_ascii=False))


if __name__ == "__main__":
    main()
