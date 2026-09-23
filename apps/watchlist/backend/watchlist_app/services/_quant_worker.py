"""Trusted bootstrap copied into an OS-isolated, disposable working directory.

This file is not the security boundary. quant_sandbox.py must apply the operating
system sandbox before this interpreter starts. It deliberately has no app imports.
"""
import json
from pathlib import Path
import resource
import sys


def main():
    request_path, output_path = sys.argv[1:3]
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    resource.setrlimit(resource.RLIMIT_CPU, (20, 20))
    resource.setrlimit(resource.RLIMIT_FSIZE, (2 * 1024 * 1024, 2 * 1024 * 1024))
    resource.setrlimit(resource.RLIMIT_NOFILE, (48, 48))
    # Linux address-space limits supplement the container's cgroup memory cap.
    # macOS reserves large virtual regions; its parent enforces resident memory.
    if sys.platform == "linux":
        resource.setrlimit(resource.RLIMIT_AS, (1536 * 1024 * 1024, 1536 * 1024 * 1024))
    request = json.loads(Path(request_path).read_text())
    # -I -S excludes PYTHONPATH, the project, .pth startup code and user packages.
    # Only the trusted installation's site-packages directories are added here.
    sys.path.extend(request.pop("package_paths", []))
    namespace = {"__name__": "__research_quant__", "inputs": request["inputs"], "params": request["params"]}
    try:
        exec(compile(request["code"], "<research-analysis>", "exec"), namespace)
        if "result" not in namespace:
            raise ValueError("代码须定义 JSON 兼容的 result 字典")
        payload = {"ok": True, "result": namespace["result"]}
        encoded = json.dumps(payload, ensure_ascii=False, allow_nan=False)
    except BaseException as error:
        encoded = json.dumps({"ok": False, "error": type(error).__name__, "message": str(error)[:1500]}, ensure_ascii=False)
    # Only this existing output file is writable. No arbitrary attachments,
    # scratch files, images, package installation or user-directory writes.
    with open(output_path, "w") as output:
        output.write(encoded)


if __name__ == "__main__":
    main()
