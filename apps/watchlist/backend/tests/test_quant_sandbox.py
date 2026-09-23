"""Exercise the actual kernel boundary, not a mocked subprocess or AST filter."""
import json
import os
from pathlib import Path
import platform
import socket
import stat
import subprocess
import sys

import pytest

from watchlist_app.services import quant_sandbox as sandbox


@pytest.fixture
def kernel_backend():
    try:
        backend = sandbox._backend()
    except sandbox.SandboxUnavailable as error:
        pytest.skip(str(error))
    capability = sandbox.availability()
    if backend == "linux-docker" and not capability["available"]:
        pytest.skip("Linux sandbox image is not provisioned: " + capability["reason"])
    assert capability["available"], capability
    return backend


def test_real_python_can_calculate_with_numpy_pandas_and_only_selected_inputs(kernel_backend):
    output = sandbox._execute('''import numpy as np
import pandas as pd
rows = pd.DataFrame(inputs["retained"]["rows"])
result = {"mean": float(np.mean(rows["value"])), "dates": rows["date"].tolist(), "scale": params["scale"]}
''', {"retained": {"rows": [{"date": "2026-01-02", "value": 10}, {"date": "2026-01-05", "value": 20}]}},
        {"scale": "original units"}, backend=kernel_backend)
    assert output == {"mean": 15, "dates": ["2026-01-02", "2026-01-05"], "scale": "original units"}


def test_real_sandbox_denies_host_files_writes_network_and_inherited_credentials(kernel_backend, tmp_path, monkeypatch):
    secret = tmp_path / "outside-secret.txt"
    secret.write_text("must-not-enter-result")
    destination = tmp_path / "outside-write.txt"
    monkeypatch.setenv("INVESTMENT_STUDIO_RESEARCH_RUN_TOKEN", "sentinel-not-a-real-token")
    monkeypatch.setenv("DEEPSEEK_API_KEY", "sentinel-not-a-real-key")
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        listener.listen(1)
        port = listener.getsockname()[1]
        code = f'''import os, socket
result = {{"run_token": os.environ.get("INVESTMENT_STUDIO_RESEARCH_RUN_TOKEN"), "api_key": os.environ.get("DEEPSEEK_API_KEY")}}
for name, fn in {{
    "host_read": lambda: open({json.dumps(str(secret))}).read(),
    "host_write": lambda: open({json.dumps(str(destination))}, "w"),
    "network": lambda: socket.create_connection(("127.0.0.1", {port}), timeout=0.5),
    "extra_file": lambda: open("another-file.json", "w"),
}}.items():
    try:
        fn()
        result[name] = "allowed"
    except OSError:
        result[name] = "denied"
'''
        output = sandbox._execute(code, {}, {}, backend=kernel_backend)
    assert output == {"run_token": None, "api_key": None, "host_read": "denied", "host_write": "denied",
                      "network": "denied", "extra_file": "denied"}
    assert not destination.exists()
    assert secret.read_text() == "must-not-enter-result"


@pytest.mark.skipif(platform.system() != "Darwin", reason="Seatbelt child-process contract")
def test_mac_sandbox_cannot_spawn_processes(kernel_backend):
    output = sandbox._execute('''import os, subprocess
result = {}
for name, action in {"fork": os.fork, "exec": lambda: subprocess.run(["/bin/echo", "hello"])}.items():
    try:
        action()
        result[name] = "allowed"
    except OSError:
        result[name] = "denied"
''', {}, {}, backend=kernel_backend)
    assert output == {"fork": "denied", "exec": "denied"}


def test_parent_rejects_symlink_output_and_never_reads_target(kernel_backend, tmp_path):
    # The code may be permitted to unlink its own output, but that never grants
    # the parent permission to follow a replacement path outside the sandbox.
    target = tmp_path / "sensitive.json"
    target.write_text('{"ok":true,"result":{"leaked":true}}')
    code = f'''import os, sys
p = sys.argv[2]
os.unlink(p)
os.symlink({json.dumps(str(target))}, p)
os._exit(0)
'''
    with pytest.raises(sandbox.AnalysisExecutionError):
        sandbox._execute(code, {}, {}, backend=kernel_backend)
    assert target.read_text() == '{"ok":true,"result":{"leaked":true}}'


def test_parent_terminates_busy_code_and_keeps_no_partial_output(kernel_backend):
    with pytest.raises(sandbox.AnalysisExecutionError, match="运行时限"):
        sandbox._execute("while True: pass", {}, {}, backend=kernel_backend, wall_seconds=0.2)


def test_removed_output_is_an_explicit_execution_error(kernel_backend):
    with pytest.raises(sandbox.AnalysisExecutionError):
        sandbox._execute("import os,sys; os.unlink(sys.argv[2]); os._exit(0)", {}, {}, backend=kernel_backend)


def test_output_size_is_enforced_by_kernel_and_parent(kernel_backend):
    with pytest.raises(sandbox.AnalysisExecutionError):
        sandbox._execute('result={"value":"x"*3000000}', {}, {}, backend=kernel_backend)


def test_invalid_and_nonfinite_output_does_not_become_evidence(kernel_backend):
    with pytest.raises(sandbox.AnalysisExecutionError, match="JSON"):
        sandbox._execute('result={"number":float("nan")}', {}, {}, backend=kernel_backend)
    with pytest.raises(sandbox.AnalysisExecutionError, match="result"):
        sandbox._execute("x=1", {}, {}, backend=kernel_backend)


def test_no_unisolated_execution_when_backend_is_missing(monkeypatch):
    monkeypatch.setattr(sandbox.platform, "system", lambda: "Unsupported")
    monkeypatch.setattr(sandbox.subprocess, "Popen", lambda *a, **k: pytest.fail("unisolated subprocess"))
    assert sandbox.availability()["available"] is False
    with pytest.raises(sandbox.SandboxUnavailable):
        sandbox.execute("result={}", {}, {})


def test_linux_command_has_real_isolation_and_no_implicit_image_pull(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/docker")
    command = sandbox._docker_command(tmp_path, "test-analysis")
    for flag in ("--network=none", "--read-only", "--cap-drop=ALL", "--security-opt=no-new-privileges",
                 "--user=65534:65534", "--memory=768m", "--memory-swap=768m", "--pids-limit=16", "--pull=never"):
        assert flag in command
    assert not any("docker.sock" in part for part in command)
    assert str(Path.home()) not in json.dumps(command)


def test_linux_image_override_uses_watchlist_environment_namespace(monkeypatch, tmp_path):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/docker")
    monkeypatch.setattr(sandbox.platform, "system", lambda: "Linux")
    monkeypatch.setenv("INVESTMENT_STUDIO_WATCHLIST_RESEARCH_QUANT_IMAGE", "review-quant:verified")
    checked_images = []
    def probe(backend, executable, image, minute):
        checked_images.append(image)
        return True
    monkeypatch.setattr(sandbox, "_probe", probe)
    assert sandbox.availability()["available"]
    assert checked_images == ["review-quant:verified"]
    assert "review-quant:verified" in sandbox._docker_command(tmp_path, "test-analysis")


def test_linux_container_readable_mount_keeps_private_host_ancestor(monkeypatch):
    monkeypatch.setattr(sandbox.shutil, "which", lambda name: "/usr/bin/docker")
    class CompletedProcess:
        returncode = 0
        pid = 123
        def poll(self): return 0
        def wait(self, **kwargs): return 0
    def start(command, *, cwd, **kwargs):
        assert stat.S_IMODE(cwd.parent.stat().st_mode) == 0o700
        assert stat.S_IMODE(cwd.stat().st_mode) == 0o755
        assert f"type=bind,source={cwd},target=/work,readonly" in command
        assert all(f"source={cwd.parent}," not in argument for argument in command)
        (cwd / "output.json").write_text('{"ok":true,"result":{"value":2}}')
        return CompletedProcess()
    monkeypatch.setattr(sandbox.subprocess, "Popen", start)
    monkeypatch.setattr(sandbox.subprocess, "run", lambda *a, **k: None)
    assert sandbox._execute("result={}", {"private": "snapshot"}, {}, backend="linux-docker") == {"value": 2}


def test_environment_is_an_allowlist(tmp_path, monkeypatch):
    monkeypatch.setenv("DATABASE_URL", "sentinel")
    monkeypatch.setenv("HTTP_PROXY", "sentinel")
    env = sandbox._environment(tmp_path)
    assert "DATABASE_URL" not in env and "HTTP_PROXY" not in env
    assert env["HOME"] == str(tmp_path)
