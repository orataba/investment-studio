"""Reuse the authorized opening check only inside one financial HTTP read.

Cache builders and the closing generation fence still check live sources.
No context is installed for commands, background jobs or unrelated requests.
"""
from contextlib import contextmanager
from contextvars import ContextVar


_opening_generations: ContextVar[dict | None] = ContextVar("portfolio_read_generations", default=None)


@contextmanager
def financial_read_context():
    token = _opening_generations.set({})
    try:
        yield
    finally:
        _opening_generations.reset(token)


def remember_opening_generation(portfolio_id: str, generation) -> None:
    values = _opening_generations.get()
    if values is not None and generation is not None:
        values[portfolio_id] = generation


def has_opening_generation(portfolio_id: str) -> bool:
    values = _opening_generations.get()
    return values is not None and portfolio_id in values
