import os
import selectors
import signal
import subprocess
import sys
import time
from types import SimpleNamespace

import pytest

from studio_runtime import processes


@pytest.mark.skipif(os.name != "posix", reason="Harness uses POSIX process groups")
@pytest.mark.parametrize("leader_exits_first", [False, True])
def test_stubborn_descendant_is_killed_even_after_its_parent_exits(leader_exits_first):
    script = """
import os, signal, sys, time
if os.fork() == 0:
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
    print("ready", flush=True)
    time.sleep(30)
elif sys.argv[1] == "False":
    time.sleep(30)
"""
    process = subprocess.Popen([sys.executable, "-c", script, str(leader_exits_first)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, start_new_session=True)
    try:
        with selectors.DefaultSelector() as selector:
            selector.register(process.stdout, selectors.EVENT_READ)
            assert selector.select(timeout=5), "Offline child did not initialize"
        assert process.stdout.readline() == "ready\n"
        if leader_exits_first:
            assert process.wait(timeout=5) == 0
        started = time.monotonic()
        processes.stop_process_group(process, grace_seconds=0.15, reap_seconds=1)
        assert 0.1 <= time.monotonic() - started < 3
        assert process.returncode == (0 if leader_exits_first else -signal.SIGTERM)
        # The descendant inherited both pipes. EOF proves it no longer holds
        # them open, not merely that the shell was reaped.
        assert process.communicate(timeout=2) == ("", "")
    finally:
        try:
            os.killpg(process.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        process.communicate(timeout=5)


@pytest.mark.parametrize("missing_group", [False, True])
def test_even_an_unreapable_parent_cannot_make_cleanup_wait_forever(monkeypatch, missing_group):
    waits, signals = [], []

    def wait(*, timeout):
        waits.append(timeout)
        raise subprocess.TimeoutExpired("offline-harness", timeout)

    def killpg(pid, sig):
        signals.append(sig)
        if missing_group:
            raise ProcessLookupError()

    monkeypatch.setattr(processes.os, "killpg", killpg)
    with pytest.raises(subprocess.TimeoutExpired):
        processes.stop_process_group(SimpleNamespace(pid=123, wait=wait), grace_seconds=0, reap_seconds=0.1)
    assert waits == ([0.1] if missing_group else [0, 0.1])
    assert signals == ([signal.SIGTERM] if missing_group else [signal.SIGTERM, 0, signal.SIGKILL])
