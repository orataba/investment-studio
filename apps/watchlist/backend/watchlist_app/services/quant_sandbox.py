"""OS-isolated Python execution for retained research inputs; never run bare code.

macOS uses Seatbelt's deny-default policy. Linux uses an explicitly provisioned
Docker image, a read-only filesystem and resource constraints. If that boundary
cannot be established, execution is unavailable, not silently downgraded.
"""
from __future__ import annotations

from functools import lru_cache
import importlib.metadata
import json
import os
from pathlib import Path
import platform
import shutil
import signal
import site
import stat
import subprocess
import sys
import tempfile
import time
from uuid import uuid4


MAX_INPUT_BYTES = 8 * 1024 * 1024
MAX_OUTPUT_BYTES = 2 * 1024 * 1024
MEMORY_BYTES = 768 * 1024 * 1024
WALL_SECONDS = 30
WORKER = Path(__file__).with_name("_quant_worker.py")
DEFAULT_IMAGE = "investment-studio-research-quant:1"


class SandboxUnavailable(RuntimeError):
    pass


class AnalysisExecutionError(ValueError):
    pass


def _json(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"))


def _environment(workdir: Path) -> dict[str, str]:
    # Do not inherit app/run tokens, provider keys, proxies or database settings.
    return {"PATH": "/usr/bin:/bin", "HOME": str(workdir), "TMPDIR": str(workdir),
            "LANG": "en_US.UTF-8", "LC_ALL": "en_US.UTF-8", "PYTHONDONTWRITEBYTECODE": "1",
            "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1", "MKL_NUM_THREADS": "1",
            "VECLIB_MAXIMUM_THREADS": "1", "NUMEXPR_NUM_THREADS": "1"}


def _package_paths():
    return sorted({str(Path(path).resolve()) for path in site.getsitepackages() if Path(path).is_dir()})


def _literal(path):
    return json.dumps(str(Path(path).resolve()))


def _mac_profile(workdir: Path) -> str:
    interpreter = Path(sys.executable).resolve()
    read_roots = [Path(sys.base_prefix).resolve(), *map(Path, _package_paths())]
    # Dynamic loader/system libraries are read-only. No /Users or /private/etc
    # root grant: runtime/package paths are exact, and app/user data stays denied.
    runtime_reads = "\n".join(f"    (subpath {_literal(path)})" for path in read_roots)
    return f'''(version 1)
(deny default)
(allow process-exec (literal {_literal(interpreter)}))
(allow signal (target self))
(allow sysctl-read)
(allow mach-lookup (global-name "com.apple.system.opendirectoryd.libinfo"))
(allow file-read*
    (literal "/")
    (subpath "/System/Library")
    (subpath "/usr/lib")
    (literal "/dev/urandom")
    (literal "/dev/random")
    (literal "/dev/null")
    (literal "/private/etc/localtime")
    (subpath {_literal(workdir)})
{runtime_reads})
(allow file-write* (literal {_literal(workdir / "output.json")}) (literal "/dev/null"))
'''


def _backend():
    system = platform.system()
    if system == "Darwin" and Path("/usr/bin/sandbox-exec").is_file():
        return "macos-seatbelt"
    if system == "Linux" and shutil.which("docker"):
        return "linux-docker"
    raise SandboxUnavailable("量化 Python 隔离环境不可用：macOS 需要 sandbox-exec，Linux 需要已配置的 Docker 运行环境。")


def _docker_command(workdir: Path, name: str):
    image = os.environ.get("INVESTMENT_STUDIO_WATCHLIST_RESEARCH_QUANT_IMAGE", DEFAULT_IMAGE)
    return [shutil.which("docker"), "run", "--rm", "--pull=never", "--name", name,
        "--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
        "--user=65534:65534", "--pids-limit=16", "--memory=768m", "--memory-swap=768m", "--cpus=1",
        "--ulimit=cpu=20:20", "--ulimit=nofile=48:48", f"--ulimit=fsize={MAX_OUTPUT_BYTES}:{MAX_OUTPUT_BYTES}",
        "--ulimit=core=0:0", "--ipc=none", "--workdir=/work", "--env=HOME=/work",
        "--env=PYTHONDONTWRITEBYTECODE=1", "--env=OPENBLAS_NUM_THREADS=1", "--env=OMP_NUM_THREADS=1",
        "--env=MKL_NUM_THREADS=1", "--env=NUMEXPR_NUM_THREADS=1",
        "--mount", f"type=bind,source={workdir},target=/work,readonly",
        "--mount", f"type=bind,source={workdir / 'output.json'},target=/output.json",
        image, "python", "-I", "-S", "/work/worker.py", "/work/input.json", "/output.json"]


def _kill(process):
    if process.poll() is None:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
    process.wait(timeout=5)


def _resident_bytes(pid):
    result = subprocess.run(["/bin/ps", "-o", "rss=", "-p", str(pid)],
                            capture_output=True, text=True, timeout=2)
    try:
        return int(result.stdout.strip()) * 1024
    except ValueError:
        return 0  # The child may already have exited; poll observes that next.


def _execute(code, inputs, params, *, backend, wall_seconds=WALL_SECONDS):
    payload = {"code": code, "inputs": inputs, "params": params,
               "package_paths": _package_paths() if backend == "macos-seatbelt" else ["/usr/local/lib/python3.12/site-packages"]}
    encoded = _json(payload)
    if len(encoded.encode()) > MAX_INPUT_BYTES:
        raise ValueError("量化输入快照超过 8 MiB；请读取并选择更具体的留存来源。")
    name = "studio-quant-" + uuid4().hex
    with tempfile.TemporaryDirectory(prefix="studio-research-quant-") as directory:
        # The outer 0700 directory protects snapshots from other host users.
        # Docker's unprivileged UID sees only the mounted inner directory.
        workdir = Path(directory).resolve() / "work"
        workdir.mkdir(mode=0o700)
        (workdir / "worker.py").write_bytes(WORKER.read_bytes())
        (workdir / "input.json").write_text(encoded)
        (workdir / "output.json").write_text("")
        if backend == "macos-seatbelt":
            command = ["/usr/bin/sandbox-exec", "-p", _mac_profile(workdir),
                       str(Path(sys.executable).resolve()), "-I", "-S", str(workdir / "worker.py"),
                       str(workdir / "input.json"), str(workdir / "output.json")]
        else:
            # The disposable container user must read only these explicitly
            # mounted inputs and write only the mounted result file.
            workdir.chmod(0o755)
            (workdir / "worker.py").chmod(0o444)
            (workdir / "input.json").chmod(0o444)
            (workdir / "output.json").chmod(0o666)
            command = _docker_command(workdir, name)
        process = None
        started = time.monotonic()
        try:
            process = subprocess.Popen(command, cwd=workdir, env=_environment(workdir),
                stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                close_fds=True, start_new_session=True)
            while process.poll() is None:
                if time.monotonic() - started > wall_seconds:
                    raise AnalysisExecutionError("量化分析超过运行时限，进程已终止。")
                if backend == "macos-seatbelt" and _resident_bytes(process.pid) > MEMORY_BYTES:
                    raise AnalysisExecutionError("量化分析超过内存限制，进程已终止。")
                time.sleep(0.05)
            if process.returncode:
                raise AnalysisExecutionError("隔离进程退出或触发资源限制；本次未生成有效量化产物。")
            output = workdir / "output.json"
            try:
                if not stat.S_ISREG(output.lstat().st_mode) or not 0 < output.lstat().st_size <= MAX_OUTPUT_BYTES:
                    raise AnalysisExecutionError("量化输出为空或超过 2 MiB；本次未生成有效产物。")
                with os.fdopen(os.open(output, os.O_RDONLY | os.O_NOFOLLOW)) as handle:
                    envelope = json.loads(handle.read(MAX_OUTPUT_BYTES + 1))
            except AnalysisExecutionError:
                raise
            except (OSError, ValueError, UnicodeError) as error:
                raise AnalysisExecutionError("量化输出不是有效 JSON。") from error
            if not isinstance(envelope, dict) or envelope.get("ok") is not True:
                message = str(envelope.get("message", "未生成 result"))[:1500] if isinstance(envelope, dict) else "输出格式错误"
                raise AnalysisExecutionError(message)
            return envelope.get("result")
        finally:
            if process is not None:
                _kill(process)
            if backend == "linux-docker":
                # Killing the Docker client does not terminate its container.
                subprocess.run([shutil.which("docker"), "rm", "-f", name], stdout=subprocess.DEVNULL,
                               stderr=subprocess.DEVNULL, timeout=10)


@lru_cache(maxsize=8)
def _probe(backend, executable, image, minute):
    # A successful command lookup is insufficient. Verify code can run AND that
    # the active kernel boundary blocks unrelated files, network and child exec.
    code = '''import os, socket, subprocess
checks = {}
for label, action in {
    "host_read": lambda: open("/etc/passwd").read(),
    "host_write": lambda: open("/tmp/studio-quant-escape", "w"),
    "network": lambda: socket.create_connection(("1.1.1.1", 443), timeout=0.2),
}.items():
    try:
        action()
        checks[label] = False
    except OSError:
        checks[label] = True
result = checks
'''
    # Container /etc/passwd is image-owned, not a host file. Its filesystem is
    # already isolated; test an unmounted host path instead.
    if backend == "linux-docker":
        code = code.replace('"/etc/passwd"', '"/host/etc/passwd"')
    else:
        code += '''
try:
    subprocess.run(["/bin/echo", "child"], check=True)
    result["child_process"] = False
except OSError:
    result["child_process"] = True
'''
    try:
        result = _execute(code, {}, {}, backend=backend, wall_seconds=8)
        keys = ["host_read", "host_write", "network"] + (["child_process"] if backend == "macos-seatbelt" else [])
        return all(result.get(key) is True for key in keys)
    except (OSError, subprocess.SubprocessError, ValueError):
        return False


def availability():
    try:
        backend = _backend()
    except SandboxUnavailable as error:
        return {"available": False, "reason": str(error), "backend": None}
    image = os.environ.get("INVESTMENT_STUDIO_WATCHLIST_RESEARCH_QUANT_IMAGE", DEFAULT_IMAGE)
    if not _probe(backend, sys.executable, image, int(time.monotonic() // 60)):
        return {"available": False, "backend": backend,
                "reason": "隔离环境自检未通过；请检查系统 sandbox 或预先构建 Docker 量化镜像。没有回退到宿主 Python。"}
    packages = {}
    if backend == "macos-seatbelt":
        for package in ("numpy", "pandas", "scipy"):
            try:
                packages[package] = importlib.metadata.version(package)
            except importlib.metadata.PackageNotFoundError:
                pass
    return {"available": True, "backend": backend, "reason": None,
            "python": platform.python_version() if backend == "macos-seatbelt" else "3.12 (container)",
            "packages": packages if backend == "macos-seatbelt" else {"numpy": "image", "pandas": "image", "scipy": "image"},
            "limits": {"wall_seconds": WALL_SECONDS, "cpu_seconds": 20, "memory_bytes": MEMORY_BYTES,
                       "input_bytes": MAX_INPUT_BYTES, "output_bytes": MAX_OUTPUT_BYTES, "output_files": 1},
            "network": False, "host_data_access": False}


def execute(code, inputs, params):
    capability = availability()
    if not capability["available"]:
        raise SandboxUnavailable(capability["reason"])
    try:
        return _execute(code, inputs, params, backend=capability["backend"]), capability
    except (OSError, subprocess.SubprocessError) as error:
        raise SandboxUnavailable("量化隔离运行环境中断，本次没有生成有效产物；没有回退到宿主执行。") from error
