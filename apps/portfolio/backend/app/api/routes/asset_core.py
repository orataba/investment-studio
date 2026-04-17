from fastapi import APIRouter

router = APIRouter()


@router.get("/schema")
def asset_core_schema() -> dict[str, object]:
    return {
        "shared_objects": [
            "AssetCore",
            "AssetIdentifier",
            "MarketDataPoint",
            "QuoteSelectionPolicy",
        ],
        "asset_core_fields": [
            "asset_id",
            "asset_name",
            "asset_type",
            "currency",
            "identifiers",
        ],
        "metric_families": ["price", "nav", "fx"],
        "quote_bases": [
            "last",
            "close",
            "adjusted_close",
            "official_nav",
            "total_return_nav",
            "spot",
            "clean_price",
            "dirty_price",
            "par",
        ],
        "quote_roles": ["trading", "valuation", "total_return", "chart", "reference"],
        "notes": [
            "Shared asset core only covers asset identity, typed market data, and the minimal selector policy used to resolve quotes by role.",
            "Portfolio-specific positions, transactions, ledger postings, risk and review remain private to the Portfolio app.",
        ],
    }


@router.get("/example")
def asset_core_example() -> dict[str, object]:
    return {
        "asset": {
            "asset_id": "fund_us_agg_bond_001",
            "asset_name": "US Aggregate Bond Fund",
            "asset_type": "fund",
            "currency": "USD",
            "identifiers": [
                {
                    "identifier_type": "ticker",
                    "identifier_value": "USABX",
                    "is_primary": True,
                },
                {
                    "identifier_type": "isin",
                    "identifier_value": "US0000000001",
                    "is_primary": False,
                },
            ],
        },
        "latest_market_data": [
            {
                "asset_id": "fund_us_agg_bond_001",
                "metric_family": "nav",
                "quote_basis": "official_nav",
                "as_of_date": "2026-04-14",
                "value": "102.340000",
                "currency": "USD",
                "provider": "manual_or_vendor",
                "status": "complete",
            }
        ],
        "quote_selection_policy": {
            "trading": ["official_nav"],
            "valuation": ["official_nav", "close"],
            "total_return": ["total_return_nav", "official_nav", "close"],
            "chart": ["total_return_nav", "official_nav", "close"],
            "reference": ["official_nav", "close"],
        },
    }
