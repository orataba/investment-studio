"""Independent local research service: python -m watchlist_app.research_worker."""
from concurrent.futures import ThreadPoolExecutor
import logging
import os
import signal
from threading import Event, Thread
from time import monotonic, sleep
from uuid import uuid4
from urllib.error import URLError
from urllib.request import urlopen

from studio_runtime.model_work import model_work_status
from watchlist_app.core.settings import get_settings
from watchlist_app.services.research_execution import claim_next, execute_claim, heartbeat, pause_pending, recover_expired, recover_undispatched

logger = logging.getLogger(__name__)


def dependencies_ready():
    """Wait out API restarts before spending a queue grant or model attempt."""
    from watchlist_app.services.research_runner import harness_available
    if not harness_available():
        return False
    endpoints = [os.environ.get("INVESTMENT_STUDIO_WATCHLIST_RESEARCH_API_BASE_URL",
                               "http://127.0.0.1:8000/api").rstrip("/") + "/health"]
    identity_url = os.environ.get("INVESTMENT_STUDIO_AUTH_URL", "").strip().rstrip("/")
    if identity_url:
        endpoints.append(identity_url.removesuffix("/auth") + "/health")
    try:
        for endpoint in endpoints:
            with urlopen(endpoint, timeout=2) as response:
                if response.status != 200:
                    return False
    except (URLError, TimeoutError, OSError):
        return False
    return True


def run_worker(stop):
    """The database gate bounds all instances, not just this process's pool."""
    settings = get_settings()
    owner = uuid4().hex
    active = {}
    next_heartbeat = monotonic()
    with ThreadPoolExecutor(max_workers=settings.research_worker_concurrency, thread_name_prefix="research-execution") as pool:
        while not stop.is_set() or active:
            for future in list(active):
                if future.done():
                    claim, cancelled = active.pop(future)
                    try:
                        future.result()
                    except Exception:
                        logger.exception("Research worker failed run_id=%s attempt=%s", claim.run_id, claim.attempt)
            paused = model_work_status()["paused"]
            if stop.is_set() or paused:
                for _, cancelled in active.values():
                    cancelled.set()
            if monotonic() >= next_heartbeat:
                for claim, cancelled in active.values():
                    try:
                        if not heartbeat(claim):
                            cancelled.set()
                    except Exception:
                        # Stop model work when ownership cannot be maintained.
                        cancelled.set()
                        logger.exception("Research heartbeat failed run_id=%s attempt=%s", claim.run_id, claim.attempt)
                if not stop.is_set() and not paused:
                    try:
                        recover_undispatched()
                    except Exception:
                        logger.exception("Research dispatch reconciliation failed")
                elif paused:
                    try:
                        pause_pending()
                    except Exception:
                        logger.exception("Research pause reconciliation failed")
                next_heartbeat = monotonic() + settings.research_worker_heartbeat_seconds
            if not stop.is_set() and not paused:
                try:
                    recover_expired()
                    while len(active) < settings.research_worker_concurrency and dependencies_ready():
                        claim = claim_next(owner)
                        if claim is None:
                            break
                        cancelled = Event()
                        active[pool.submit(execute_claim, claim, cancelled)] = (claim, cancelled)
                except Exception:
                    logger.exception("Research queue polling failed")
            if active and stop.is_set():
                # Event.wait would return immediately after stop; avoid a busy drain.
                sleep(0.1)
            else:
                stop.wait(1)


def main():
    logging.basicConfig(level=logging.INFO)
    stop = Event()
    for signum in (signal.SIGINT, signal.SIGTERM):
        signal.signal(signum, lambda *_: stop.set())
    # Scheduling is separate from admission; its thread only persists queue work.
    # The same worker capacity serves interactive and scheduled jobs.
    scheduler = None
    if get_settings().research_worker_enabled:
        from watchlist_app.services.sector_research import run_daily_reviews
        def schedule():
            while not stop.is_set():
                try:
                    if dependencies_ready():
                        run_daily_reviews(stop)
                except Exception:
                    logger.exception("Research schedule scan failed")
                stop.wait(60)
        scheduler = Thread(target=schedule, name="research-scheduler", daemon=True)
        scheduler.start()
    run_worker(stop)
    if scheduler:
        scheduler.join(timeout=5)


if __name__ == "__main__":
    main()
