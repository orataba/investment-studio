"""Futu market-data access; this package does not provide brokerage trading."""

from .oauth import FutuOAuth, FutuOAuthError

__all__ = ["FutuOAuth", "FutuOAuthError"]
