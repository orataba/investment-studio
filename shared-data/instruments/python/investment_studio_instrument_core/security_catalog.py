"""Narrow bridge to the data owner's securities commands.

The child command loads its own data environment; Consumers do not own the
catalog database, provider credentials, or security registration implementation.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class SecuritySearchResult(BaseModel):
    instrument_type: Literal["equity", "etf"]
    symbol: str
    catalog_provider: Literal["fmp"]
    catalog_symbol: str
    name: str
    exchange_code: str
    exchange_label: str
    market: str
    currency: str
    currency_verified: bool = True
    country: str | None = None
    sector: str | None = None
    industry: str | None = None
    existing_instrument_id: str | None = None


class SecuritySearchResponse(BaseModel):
    results: list[SecuritySearchResult]
    catalog_errors: dict[Literal["equity", "etf"], str] = Field(default_factory=dict)


class SecurityMaterializeRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    instrument_type: Literal["equity", "etf"]
    catalog_provider: Literal["fmp"]
    catalog_symbol: str = Field(min_length=1, max_length=100)

    @field_validator("catalog_symbol")
    @classmethod
    def normalize_symbol(cls, value: str) -> str:
        value = value.strip().upper()
        if not value:
            raise ValueError("catalog_symbol must not be blank")
        return value


class SecurityCatalogError(RuntimeError):
    def __init__(self, message: str, status_code: int = 502):
        super().__init__(message)
        self.status_code = status_code


def _run_command(arguments: list[str], *, payload: dict | None = None) -> dict:
    command = Path(__file__).resolve().parents[4] / "bin" / "investment-studio"
    try:
        result = subprocess.run(
            [str(command), "data", "securities", *arguments],
            input=json.dumps(payload) if payload is not None else None,
            # Runtime loaders preserve caller values. Do not let an app's
            # service identity/configuration override the data owner's env file.
            env={key: value for key, value in os.environ.items()
                 if not key.startswith("INVESTMENT_STUDIO_")},
            capture_output=True,
            text=True,
            timeout=120 if payload is not None else 30,
            check=False,
        )
    except subprocess.TimeoutExpired as error:
        detail = (
            "证券登记响应超时，登记可能已完成；请重新搜索确认。"
            if payload is not None else "证券目录查询超时，请重试。"
        )
        raise SecurityCatalogError(detail, 504) from error
    except OSError as error:
        raise SecurityCatalogError("无法启动证券目录维护入口。") from error
    try:
        decoded = json.loads(result.stdout)
        if not isinstance(decoded, dict):
            raise ValueError("Expected object")
    except (ValueError, TypeError) as error:
        raise SecurityCatalogError("证券目录维护入口未返回有效结果。") from error
    if result.returncode:
        # Only expose structured command errors, never stderr/tracebacks or the
        # child environment. Exit 3 explicitly means the write already happened.
        message = str(decoded.get("error") or "证券目录操作失败。")
        if result.returncode == 3:
            message = "证券已登记，但下游重算确认失败；请重新搜索确认。"
        raise SecurityCatalogError(message, 422 if result.returncode == 2 else 502)
    return decoded


def search_catalog(query: str, limit: int) -> SecuritySearchResponse:
    result = _run_command(["search", "--limit", str(limit), "--", query])
    try:
        return SecuritySearchResponse.model_validate(result)
    except ValueError as error:
        raise SecurityCatalogError("证券目录返回的数据格式无效。") from error


def materialize_catalog_security(payload: SecurityMaterializeRequest) -> dict:
    return _run_command(
        ["add", "--input", "-", "--apply"],
        payload={**payload.model_dump(), "refresh_eod": True},
    )
