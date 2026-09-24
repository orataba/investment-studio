from datetime import UTC, datetime

import pytest
from studio_market.config import MarketSettings
from studio_market.numeric import NumericStore

from watchlist_app.services.research_data import DATA_METHODS, StoredDataRequest, stored_market_data
from watchlist_app.services.research_notebook import ResearchNotebook, research_sources, validate_notebook


@pytest.fixture
def store(tmp_path):
    store = NumericStore(MarketSettings(f"sqlite:///{tmp_path / 'market.db'}", tmp_path / "data"))
    store.create_schema_for_testing()
    yield store
    store.close()


def stamp(day):
    return datetime(2026, 9, day, tzinfo=UTC)


def test_catalogue_distinguishes_supported_contract_from_actual_coverage(store):
    catalogue = stored_market_data(StoredDataRequest(), as_of=stamp(24))
    assert {row["dataset"] for row in catalogue["datasets"]} == set(DATA_METHODS)
    assert all(row["coverage"] == "not_checked" for row in catalogue["datasets"])
    missing = stored_market_data(StoredDataRequest(dataset="cn_financials"), as_of=stamp(24), store=store)
    assert missing["data"]["status"] == "unavailable" and not missing["data"]["rows"]


def test_commodity_facts_keep_units_paging_and_information_clock(store):
    for day, value in ((22, 12), (25, 18)):
        store.ingest("cn_futures_observations", [[{"dataset": "inventory", "product_id": "CU",
            "observation_key": key, "snapshot_date": f"2026-09-{day}", "observation_date": "2026-09-20",
            "value": value + index, "unit": "万吨"} for index, key in enumerate(("exchange", "warehouse"))]],
            source="fixture", observed_at=stamp(day))
    request = StoredDataRequest(action="records", dataset="cn_futures_observations", symbols=["CU"], limit=1)
    first = stored_market_data(request, as_of=stamp(24), store=store)
    assert first["data"]["next_offset"] == 1 and first["data"]["total"] == 2
    second = stored_market_data(request.model_copy(update={"offset": 1}), as_of=stamp(24), store=store)
    rows = first["data"]["rows"] + second["data"]["rows"]
    assert {row["value"] for row in rows} == {12, 13}
    assert all(row["unit"] == "万吨" and row["source_id"] and row["batch_id"] for row in rows)
    assert second["data"]["next_offset"] is None
    context = {"cutoff": stamp(24).isoformat(), "computed_metrics": [first]}
    sources = research_sources(context, "run")
    validate_notebook(ResearchNotebook(source_ids=[first["source_id"]]), "commodity-etf", sources)


def test_dataset_and_date_errors_do_not_fall_back_to_different_facts():
    with pytest.raises(ValueError, match="已支持"):
        StoredDataRequest(dataset="private_portfolio")
    with pytest.raises(ValueError, match="指定数据集"):
        StoredDataRequest(action="records")
    with pytest.raises(ValueError, match="起始日期"):
        StoredDataRequest(dataset="macro_series", start="2026-09-25", end="2026-09-24")
