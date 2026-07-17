"""Version contract for Watchlist-derived read models.

Source watermarks only identify changes in market data.  They cannot invalidate
materializations when quote selection, return windows, or another calculation
policy changes without a source-data write.  Bump this value whenever such a
policy change requires every instrument read model to be rebuilt.
"""

WATCHLIST_MATERIALIZATION_VERSION = "watchlist-materialization/v2"
UNVERSIONED_MATERIALIZATION = "unversioned"
