import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys

import pytest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts/watchlist_test_shard.py"
spec = importlib.util.spec_from_file_location("watchlist_test_shard", SCRIPT)
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


def test_shards_cover_each_test_file_once_including_new_nested_tests(tmp_path):
    names = ["test_z.py", "test_a.py", "nested/test_new.py", "test_b.py", "test_c.py"]
    for name in [*names, "conftest.py", "helper.py"]:
        path = tmp_path / name
        path.parent.mkdir(exist_ok=True)
        path.touch()
    first, second = (module.select_files(tmp_path, shard) for shard in (1, 2))
    assert set(first).isdisjoint(second)
    assert set(first) | set(second) == {tmp_path / name for name in names}
    assert first == module.select_files(tmp_path, 1)
    assert abs(len(first) - len(second)) <= 1


def test_invalid_or_empty_shard_cannot_silently_pass(tmp_path):
    with pytest.raises(ValueError, match="1 or 2"):
        module.select_files(tmp_path, 0)
    with pytest.raises(ValueError, match="no test files"):
        module.select_files(tmp_path, 1)


@pytest.mark.parametrize("shard", [None, 1, 2])
def test_verification_entrypoint_keeps_full_default_and_passes_exact_shard_files(tmp_path, shard):
    invocation = tmp_path / "pytest-arguments.json"
    wrapper = tmp_path / "python"
    wrapper.write_text(f"#!{sys.executable}\n" + '''import json, os, subprocess, sys
from pathlib import Path
if sys.argv[1:3] == ["-m", "pytest"]:
    Path(os.environ["TEST_PYTEST_ARGUMENTS"]).write_text(json.dumps(sys.argv[1:]))
else:
    raise SystemExit(subprocess.run([sys.executable, *sys.argv[1:]]).returncode)
''')
    wrapper.chmod(0o700)
    root = SCRIPT.parents[2]
    subprocess.run(["bash", str(root / "infra/scripts/verify_repository.sh"), "backend", "watchlist",
                    *([str(shard)] if shard else [])], check=True, capture_output=True, text=True,
                   env={**os.environ, "PROJECT_ROOT": str(root), "PYTHON_BIN": str(wrapper),
                        "TEST_PYTEST_ARGUMENTS": str(invocation)})
    arguments = json.loads(invocation.read_text())
    expected = ["-m", "pytest", "-m", "not postgresql_integration", "--strict-markers", "-ra"]
    if shard:
        backend = root / "apps/watchlist/backend"
        expected += [path.relative_to(backend).as_posix() for path in module.select_files(backend / "tests", shard)]
    assert arguments == expected
