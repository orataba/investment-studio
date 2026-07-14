from __future__ import annotations

import json
from urllib.error import URLError

import pytest

from platform_app.services import watchlist_recalc_sender


class _StubSettings:
    watchlist_api_url = "http://watchlist.local"


class _StubResponse:
    def __init__(self, payload: object) -> None:
        self.payload = payload

    def __enter__(self) -> "_StubResponse":
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # type: ignore[no-untyped-def]
        return None

    def read(self) -> bytes:
        return json.dumps(self.payload).encode("utf-8")


def _acknowledgement(
    *,
    enqueued: list[str] | None = None,
    coalesced: list[str] | None = None,
    existing: list[str] | None = None,
    ignored: list[str] | None = None,
) -> dict[str, object]:
    return {
        "requested_count": 1,
        "accepted_count": 1,
        "enqueued_instrument_ids": enqueued or [],
        "coalesced_instrument_ids": coalesced or [],
        "existing_instrument_ids": existing or [],
        "ignored_instrument_ids": ignored or [],
        "missing_instrument_ids": [],
    }


def test_sender_uses_outbox_event_id_as_trigger_reference(monkeypatch) -> None:
    requests: list[tuple[str, float, dict[str, object]]] = []

    def fake_urlopen(request, timeout):  # type: ignore[no-untyped-def]
        payload = json.loads(request.data.decode("utf-8"))
        requests.append((request.full_url, timeout, payload))
        return _StubResponse(_acknowledgement(enqueued=["fund-a"]))

    monkeypatch.setattr(
        watchlist_recalc_sender,
        "get_settings",
        lambda: _StubSettings(),
    )
    monkeypatch.setattr(watchlist_recalc_sender, "urlopen", fake_urlopen)

    acknowledgement = watchlist_recalc_sender.send_watchlist_market_data_recalc(
        event_id=" event-1 ",
        instrument_id=" fund-a ",
        timeout_seconds=3.0,
    )

    assert acknowledgement.event_id == "event-1"
    assert acknowledgement.instrument_id == "fund-a"
    assert requests == [
        (
            "http://watchlist.local/api/recalc/bulk",
            3.0,
            {
                "instrument_ids": ["fund-a"],
                "job_type": "all",
                "trigger_type": "market_data_refresh",
                "trigger_ref_type": "instrument_registry_outbox",
                "trigger_ref_id": "event-1",
            },
        )
    ]


@pytest.mark.parametrize(
    "payload",
    [
        {
            **_acknowledgement(enqueued=["fund-a"]),
            "requested_count": 0,
        },
        {
            **_acknowledgement(enqueued=["fund-a"]),
            "accepted_count": 0,
        },
        {
            **_acknowledgement(enqueued=["fund-a"]),
            "missing_instrument_ids": ["fund-a"],
        },
        {
            **_acknowledgement(enqueued=["fund-a"]),
            "deferred_instrument_ids": ["fund-a"],
        },
        _acknowledgement(enqueued=["different-fund"]),
        _acknowledgement(enqueued=["fund-a"], existing=["fund-a"]),
        {
            **_acknowledgement(enqueued=["fund-a"]),
            "ignored_instrument_ids": "fund-a",
        },
        {"requested_count": 1, "accepted_count": 1},
        [],
    ],
)
def test_sender_rejects_incomplete_acknowledgement(monkeypatch, payload) -> None:
    monkeypatch.setattr(
        watchlist_recalc_sender,
        "get_settings",
        lambda: _StubSettings(),
    )
    monkeypatch.setattr(
        watchlist_recalc_sender,
        "urlopen",
        lambda *_args, **_kwargs: _StubResponse(payload),
    )

    with pytest.raises(watchlist_recalc_sender.WatchlistRecalcDeliveryError):
        watchlist_recalc_sender.send_watchlist_market_data_recalc(
            event_id="event-1",
            instrument_id="fund-a",
        )


@pytest.mark.parametrize("disposition", ["coalesced", "existing", "ignored"])
def test_sender_accepts_one_explicit_terminal_disposition(
    monkeypatch,
    disposition: str,
) -> None:
    payload = _acknowledgement(
        coalesced=["fund-a"] if disposition == "coalesced" else None,
        existing=["fund-a"] if disposition == "existing" else None,
        ignored=["fund-a"] if disposition == "ignored" else None,
    )
    monkeypatch.setattr(
        watchlist_recalc_sender,
        "get_settings",
        lambda: _StubSettings(),
    )
    monkeypatch.setattr(
        watchlist_recalc_sender,
        "urlopen",
        lambda *_args, **_kwargs: _StubResponse(payload),
    )

    acknowledgement = watchlist_recalc_sender.send_watchlist_market_data_recalc(
        event_id="event-1",
        instrument_id="fund-a",
    )

    assert acknowledgement.instrument_id == "fund-a"


def test_sender_surfaces_transport_failure(monkeypatch) -> None:
    monkeypatch.setattr(
        watchlist_recalc_sender,
        "get_settings",
        lambda: _StubSettings(),
    )
    monkeypatch.setattr(
        watchlist_recalc_sender,
        "urlopen",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(URLError("unavailable")),
    )

    with pytest.raises(
        watchlist_recalc_sender.WatchlistRecalcDeliveryError,
        match="unavailable",
    ):
        watchlist_recalc_sender.send_watchlist_market_data_recalc(
            event_id="event-1",
            instrument_id="fund-a",
        )


def test_sender_requires_positive_timeout() -> None:
    with pytest.raises(ValueError, match="positive"):
        watchlist_recalc_sender.send_watchlist_market_data_recalc(
            event_id="event-1",
            instrument_id="fund-a",
            timeout_seconds=0,
        )
