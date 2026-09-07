import json

import duckdb
import pytest

from watchlist_app.services.sector_market_data import read_sector_market_data


@pytest.fixture
def market_database(tmp_path):
    path = tmp_path / "market.duckdb"
    with duckdb.connect(str(path)) as connection:
        connection.execute("""
            CREATE TABLE etf_info (symbol VARCHAR, name VARCHAR, collected_at TIMESTAMPTZ);
            INSERT INTO etf_info VALUES ('XLK', 'Technology ETF', '2026-09-05T00:00:00Z');
            CREATE TABLE etf_holdings_current (
                etf_symbol VARCHAR, holding_key VARCHAR, holding_symbol VARCHAR,
                holding_name VARCHAR, weight_percent DOUBLE, snapshot_date DATE,
                source_dataset VARCHAR, raw_sha256 VARCHAR, collected_at TIMESTAMPTZ);
            INSERT INTO etf_holdings_current VALUES
                ('XLK','stock','AAA','Alpha',99.5,'2026-09-04','fmp_etf_current_holdings','holdings-source','2026-09-05T00:00:00Z'),
                ('XLK','cash',NULL,'US DOLLAR',-0.1,'2026-09-04','fmp_etf_current_holdings','holdings-source','2026-09-05T00:00:00Z'),
                ('XLK','fund',NULL,'SSI US GOV MONEY MARKET CLASS',0.4,'2026-09-04','fmp_etf_current_holdings','holdings-source','2026-09-05T00:00:00Z'),
                ('XLK','future','IXTU6','XAK TECHNOLOGY SEP26',0.1,'2026-09-04','fmp_etf_current_holdings','holdings-source','2026-09-05T00:00:00Z'),
                ('XLK','contra','2602335D','CONTRA HOLOGIC',0,'2026-09-04','fmp_etf_current_holdings','holdings-source','2026-09-05T00:00:00Z'),
                ('XLK','unknown','BBB','Unresolved holding',0.1,'2026-09-04','fmp_etf_current_holdings','holdings-source','2026-09-05T00:00:00Z');
            CREATE TABLE company_profiles (
                symbol VARCHAR, company_name VARCHAR, sector VARCHAR, industry VARCHAR,
                description VARCHAR, is_etf BOOLEAN, is_fund BOOLEAN, collected_at TIMESTAMPTZ);
            INSERT INTO company_profiles VALUES
                ('AAA','Alpha','Technology','Software','Business description',false,false,'2026-08-10T00:00:00Z');
            CREATE TABLE us_eod_daily (
                symbol VARCHAR, date DATE, close DOUBLE, adjusted_close DOUBLE,
                source_dataset VARCHAR, collected_at TIMESTAMPTZ);
            INSERT INTO us_eod_daily VALUES
                ('XLK','2026-09-03',100,99,'fmp_us_eod','2026-09-04T00:00:00Z'),
                ('XLK','2026-09-04',102,101,'fmp_us_eod','2026-09-05T00:00:00Z'),
                ('AAA','2026-09-04',50,48,'fmp_us_eod','2026-09-05T00:00:00Z');
            CREATE TABLE dataset_state (
                dataset VARCHAR, status VARCHAR, last_success_at TIMESTAMPTZ,
                last_data_date DATE, row_count BIGINT);
            INSERT INTO dataset_state VALUES
                ('fmp_us_eod','failed','2026-09-05T04:00:00Z','2026-09-04',3),
                ('fmp_analyst_estimates','ok','2026-09-05T04:00:00Z','2099-12-31',2);
        """)
        metrics = [f"{metric}_{bound} DOUBLE" for metric in
                   ("revenue", "ebitda", "ebit", "net_income", "eps")
                   for bound in ("low", "high", "avg")]
        connection.execute(
            "CREATE TABLE analyst_estimates_current (symbol VARCHAR, estimate_period VARCHAR, "
            "target_period_end DATE, " + ", ".join(metrics) + ", num_analysts_revenue BIGINT, "
            "num_analysts_eps BIGINT, source_dataset VARCHAR, raw_sha256 VARCHAR, "
            "collected_at TIMESTAMPTZ, historical_use VARCHAR)"
        )
        connection.execute("""
            INSERT INTO analyst_estimates_current
                (symbol,estimate_period,target_period_end,revenue_avg,eps_avg,
                 num_analysts_revenue,num_analysts_eps,source_dataset,raw_sha256,collected_at,historical_use)
            VALUES ('AAA','annual','2099-12-31',1000,2.5,12,11,'fmp_analyst_estimates_bulk',
                    'estimate-source','2026-08-10T00:00:00Z','since_capture'),
                   ('AAA','quarter','2000-12-31',200,0.5,10,9,'fmp_analyst_estimates_bulk',
                    'old-estimate-source','2026-08-10T00:00:00Z','since_capture')
        """)
    return path


def test_preserves_holdings_clocks_and_estimate_meaning(market_database, monkeypatch):
    original_connect = duckdb.connect
    connect_modes = []

    def connect(*args, **kwargs):
        connect_modes.append(kwargs.get("read_only"))
        return original_connect(*args, **kwargs)

    monkeypatch.setattr(duckdb, "connect", connect)
    result = read_sector_market_data(market_database, " xlk ")
    assert connect_modes == [True]
    json.dumps(result, allow_nan=False)
    assert result["ticker"] == "XLK"
    holdings = {row["holding_key"]: row for row in result["holdings"]}
    assert len(holdings) == 6
    assert sum(h["weight_percent"] for h in holdings.values()) == pytest.approx(100)
    assert {key: value["holding_type"] for key, value in holdings.items()} == {
        "stock": "equity", "cash": "cash", "fund": "fund", "future": "future",
        "contra": "other", "unknown": "unclassified",
    }
    assert holdings["cash"]["weight_percent"] == -0.1
    assert holdings["future"]["company_profile"] is None
    stock = holdings["stock"]
    assert stock["snapshot_date"] == "2026-09-04"
    assert stock["company_profile"]["collected_at"] == "2026-08-10T00:00:00+00:00"
    estimate = stock["annual_estimates"][0]
    assert estimate["target_period_end"] == "2099-12-31"
    assert estimate["collected_at"] == "2026-08-10T00:00:00+00:00"
    assert estimate["revenue_avg"] == 1000 and estimate["eps_avg"] == 2.5
    assert estimate["raw_sha256"] == "estimate-source"
    assert estimate["currency"] is None and estimate["currency_status"] == "not_supplied"
    assert stock["quarterly_estimates"][0]["target_period_end"] == "2000-12-31"
    assert stock["latest_price"]["close"] == 50
    assert stock["latest_price"]["adjusted_close"] == 48
    assert result["etf"]["price_coverage"] == {
        "first_date": "2026-09-03", "last_date": "2026-09-04", "observations": 2,
    }
    assert {row["kind"] for row in result["gaps"]} == {
        "unclassified_holding", "no_forward_quarter_estimates",
    }
    assert next(x for x in result["dataset_status"] if x["dataset"] == "fmp_us_eod")["status"] == "failed"
    # The reader closes its connection, allowing the source owner to write again.
    with original_connect(str(market_database)) as connection:
        assert connection.execute("SELECT count(*) FROM etf_holdings_current").fetchone()[0] == 6


def test_readable_source_with_missing_sector_returns_visible_gaps(market_database):
    result = read_sector_market_data(market_database, "XLE")
    assert result["holdings"] == []
    assert result["etf"]["info"] is None
    assert {row["kind"] for row in result["gaps"]} == {
        "missing_etf_info", "missing_holdings", "missing_price",
    }


def test_unsupported_symbol_is_rejected_and_missing_database_is_not_created(tmp_path):
    path = tmp_path / "absent.duckdb"
    with pytest.raises(ValueError, match="11 US"):
        read_sector_market_data(path, "XLK'; DROP TABLE etf_info;--")
    with pytest.raises(duckdb.IOException):
        read_sector_market_data(path, "XLK")
    assert not path.exists()


def test_company_snapshot_retains_all_periods_and_source_provenance(market_database):
    from watchlist_app.services.sector_research import sector_snapshot
    _, evidence, companies = sector_snapshot("xlk", market_database)
    company = companies["AAA"]
    assert company["quarterly_estimates"][0]["target_period_end"] == "2000-12-31"
    assert company["annual_estimates"][0]["source_dataset"] == "fmp_analyst_estimates_bulk"
    assert company["annual_estimates"][0]["raw_sha256"] == "estimate-source"
    assert company["annual_estimates"][0]["estimate_period"] == "annual"
    assert company["annual_estimates"][0]["currency"] is None
    assert evidence["source"]["read_at"] != company["annual_estimates"][0]["collected_at"]
