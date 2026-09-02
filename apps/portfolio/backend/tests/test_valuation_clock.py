from datetime import UTC, date, datetime
from zoneinfo import ZoneInfoNotFoundError

import pytest

from portfolio_app.services import valuation_clock


def test_portfolio_valuation_today_uses_portfolio_timezone(monkeypatch) -> None:
    class FrozenDatetime(datetime):
        @classmethod
        def now(cls, tz=None):
            instant = cls(2026, 9, 1, 16, 30, tzinfo=UTC)
            return instant.astimezone(tz) if tz is not None else instant

    monkeypatch.setattr(valuation_clock, "datetime", FrozenDatetime)

    assert valuation_clock.portfolio_valuation_today("Asia/Shanghai") == date(2026, 9, 2)
    assert valuation_clock.portfolio_valuation_today("America/New_York") == date(2026, 9, 1)


def test_portfolio_valuation_today_rejects_an_invalid_timezone() -> None:
    with pytest.raises(ZoneInfoNotFoundError):
        valuation_clock.portfolio_valuation_today("Not/A_Timezone")
