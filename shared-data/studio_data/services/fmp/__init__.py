"""Shared direct-FMP client. Acquisition and persistence use their own modules."""

from studio_data.services.fmp.client import FmpApiError, FmpClient

__all__ = ["FmpApiError", "FmpClient"]
