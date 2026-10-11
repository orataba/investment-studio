"""Bounded teardown of application-owned POSIX process groups."""
import os
import signal
import subprocess
import time


def stop_process_group(process, *, grace_seconds=5, reap_seconds=5):
    """Stop a process launched with start_new_session, including its descendants.

    The group keeps its TERM grace period even when the shell exits first.
    Reaping the direct child is bounded too; pipe draining belongs to the caller.
    """
    deadline = time.monotonic() + grace_seconds
    try:
        os.killpg(process.pid, signal.SIGTERM)
    except ProcessLookupError:
        process.wait(timeout=reap_seconds)
        return
    try:
        process.wait(timeout=grace_seconds)
    except subprocess.TimeoutExpired:
        pass
    while True:
        try:
            os.killpg(process.pid, 0)
        except ProcessLookupError:
            break
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
            break
        time.sleep(min(0.05, remaining))
    process.wait(timeout=reap_seconds)
