from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy.engine import make_url


@dataclass(frozen=True)
class MarketSettings:
    database_url: str
    data_root: Path
    fmp_api_key_file: Path | None = None
    tushare_token_file: Path | None = None
    datahub_api_key_file: Path | None = None
    datahub_api_url: str = "http://datahubco.com/app-api/openapi/v1/tushare"
    gtja_access_key_id_file: Path | None = None
    gtja_access_key_secret_file: Path | None = None

    def __post_init__(self) -> None:
        if not self.database_url:
            raise ValueError("INVESTMENT_STUDIO_MARKET_DATABASE_URL is required")
        if make_url(self.database_url).password is not None:
            raise ValueError("Keep the database password outside DATABASE_URL; use PostgreSQL credential configuration")
        object.__setattr__(self, "data_root", Path(self.data_root).expanduser())

    @classmethod
    def from_environment(cls, database_url: str | None = None, data_root: str | Path | None = None) -> "MarketSettings":
        prefix = "INVESTMENT_STUDIO_MARKET_"
        def file(name: str) -> Path | None:
            value = os.environ.get(prefix + name)
            return Path(value).expanduser() if value else None
        return cls(
            database_url=database_url or os.environ.get(prefix + "DATABASE_URL", ""),
            data_root=Path(data_root or os.environ.get(prefix + "DATA_ROOT", "~/.local/share/investment-studio/market-data")).expanduser(),
            fmp_api_key_file=file("FMP_API_KEY_FILE"),
            tushare_token_file=file("TUSHARE_TOKEN_FILE"),
            datahub_api_key_file=file("DATAHUB_API_KEY_FILE"),
            datahub_api_url=os.environ.get(prefix+"DATAHUB_API_URL", "http://datahubco.com/app-api/openapi/v1/tushare"),
            gtja_access_key_id_file=file("GTJA_ACCESS_KEY_ID_FILE"),
            gtja_access_key_secret_file=file("GTJA_ACCESS_KEY_SECRET_FILE"),
        )

    def read_secret(self, name: str) -> str:
        path = getattr(self, name + "_file")
        if path is None:
            raise ValueError(f"Configure INVESTMENT_STUDIO_MARKET_{name.upper()}_FILE")
        value = Path(path).read_text(encoding="utf-8").strip()
        if not value:
            raise ValueError(f"Empty credential file for {name}")
        return value
