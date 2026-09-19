"""Bounded computed values, reusable only while their live source key agrees.

Callers own source identity and response-copy semantics. This module knows no
portfolio, transaction or market-data rules and never treats elapsed time as
evidence that financial inputs are unchanged.
"""
from __future__ import annotations

from collections import OrderedDict
from collections.abc import Callable, Hashable
from concurrent.futures import Future
from dataclasses import dataclass
import json
from threading import RLock
from typing import TypeVar


T = TypeVar("T")
SourceKey = tuple[Hashable, ...]
SOURCE_CACHE_MAX_ENTRIES = 64
SOURCE_CACHE_MAX_TOTAL_BYTES = 32 * 1024 * 1024


@dataclass
class _CacheEntry:
    value: object
    approx_size_bytes: int


_cache: OrderedDict[SourceKey, _CacheEntry] = OrderedDict()
_cache_lock = RLock()
_cache_total_size_bytes = 0
_inflight: dict[SourceKey, Future[object]] = {}


def _write_cache(cache_key: SourceKey, value: object) -> None:
    global _cache_total_size_bytes
    approx_size_bytes = len(json.dumps(
        value, default=str, ensure_ascii=False, separators=(",", ":"),
    ).encode("utf-8"))
    if approx_size_bytes > SOURCE_CACHE_MAX_TOTAL_BYTES:
        return
    with _cache_lock:
        previous = _cache.pop(cache_key, None)
        if previous is not None:
            _cache_total_size_bytes -= previous.approx_size_bytes
        _cache[cache_key] = _CacheEntry(value, approx_size_bytes)
        _cache_total_size_bytes += approx_size_bytes
        while len(_cache) > SOURCE_CACHE_MAX_ENTRIES or _cache_total_size_bytes > SOURCE_CACHE_MAX_TOTAL_BYTES:
            _, evicted = _cache.popitem(last=False)
            _cache_total_size_bytes -= evicted.approx_size_bytes


def get_source_value(*, source_key: Callable[[], SourceKey | None], builder: Callable[[], T]) -> T:
    while True:
        cache_key = source_key()
        pending = None
        if cache_key is not None:
            with _cache_lock:
                cached = _cache.get(cache_key)
                if cached is not None:
                    _cache.move_to_end(cache_key)
                    return cached.value  # type: ignore[return-value]
                pending = _inflight.get(cache_key)
                owns_build = pending is None
                if owns_build:
                    pending = Future()
                    _inflight[cache_key] = pending
            if not owns_build:
                value = pending.result()
                if cache_key == source_key():
                    return value  # type: ignore[return-value]
                # The source changed while waiting. Re-enter with today's
                # actual key instead of inheriting the old calculation.
                continue
        try:
            value = builder()
            refreshed_key = source_key()
            if cache_key is not None and cache_key == refreshed_key:
                _write_cache(cache_key, value)
            if pending is not None:
                pending.set_result(value)
            return value
        except BaseException as exc:
            if pending is not None:
                pending.set_exception(exc)
            raise
        finally:
            if pending is not None:
                with _cache_lock:
                    _inflight.pop(cache_key, None)
