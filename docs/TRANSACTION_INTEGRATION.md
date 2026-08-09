# Portfolio transaction integration

External projects write transactions through the Portfolio HTTP API, not directly to database tables. The API applies the same validation, audit, idempotency, and snapshot invalidation rules to manual entry, direct API writes, and CSV imports.

## Write paths

- Single fact: `POST /api/portfolios/{portfolio_id}/transactions`
- CSV preview: `POST /api/portfolios/{portfolio_id}/transactions/csv/preview`
- CSV import: `POST /api/portfolios/{portfolio_id}/transactions/csv/import`
- CSV template: `GET /api/portfolios/{portfolio_id}/transactions/csv-template`
- CSV download: `GET /api/portfolios/{portfolio_id}/transactions.csv`

Send a unique `Idempotency-Key` header with writes. An upstream project should also send `source_system` and a stable `external_reference`; that pair is unique inside a portfolio.

Example: sell three Put contracts to open, with a multiplier of 100:

```json
{
  "transaction_type": "option_write",
  "trade_date": "2026-02-15",
  "settlement_date": "2026-02-15",
  "account_id": "broker-us-core",
  "settlement_cash_account_id": "cash-usd-main",
  "instrument_id": "option-demo-put-001",
  "quantity": 3,
  "price": 2,
  "gross_amount": 600,
  "fees": 2,
  "taxes": 0,
  "currency": "USD",
  "source_system": "colleague_project",
  "external_reference": "PUT-STO-001",
  "note": "Sell three Put contracts to open"
}
```

## One row, one fact

Portfolio does not create or bind multi-leg derivative transactions, and the transaction schema has no derivative relation or event-group field. If two rows are economically related, record both independently and explain the relationship in `note` when useful.

Examples:

- Long Call exercise: close the Call with `option_long_exercise`, then enter a separate stock `buy` at the strike price.
- Short Call assignment: close the short Call with `option_assignment`, then enter a separate stock `sell` at the strike price.
- FCN knock-in with asset delivery: close the FCN with result `fcn_knock_in`, then enter a separate asset `buy`.

The system does not require matching quantities, timestamps, references, or notes between those rows.

## Option facts

Every option instrument in Registry contains its Call/Put type, underlying, expiry, strike, multiplier, settlement type, and currency. The instrument identity determines whether the row is a Call or Put; the transaction type determines the action.

| Stored `transaction_type` | Option action | Quantity |
|---|---|---|
| `buy` | buy to open | contracts |
| `sell` | sell to close | contracts |
| `option_write` | sell to open | contracts |
| `option_buy_to_close` | buy to close | contracts |
| `maturity_redemption` + `option_long_expiry` | close expired long option | contracts |
| `maturity_redemption` + `option_long_exercise` | close exercised long option | contracts |
| `lifecycle_event` + `option_writer_expiry` | close expired short option | contracts |
| `lifecycle_event` + `option_assignment` | close assigned short option | contracts |

Both Call and Put contracts, long and short, physical and cash settlement are supported. For premium trades, `price` is premium per underlying unit and:

`gross_amount = contract quantity × premium price × Registry contract multiplier`

Long options remain at transaction cost between recorded events. Short options appear as negative contract positions with a remaining premium-basis liability. No daily option market price or covered-position test is required.

## FCN facts

| Stored fact | Meaning |
|---|---|
| `buy` | enter/buy the FCN contract |
| `coupon` | FCN interest income |
| `maturity_redemption` + blank/`fcn_maturity` | close normally |
| `maturity_redemption` + `fcn_knock_in` | close with knock-in result |
| `maturity_redemption` + `fcn_knock_out` | close with knock-out result |

FCNs remain at transaction cost between recorded events. Portfolio does not verify barrier levels and does not infer or bind any delivered asset.

## CSV fields

Required columns are `transaction_type`, `trade_date`, `account_id`, `gross_amount`, and `currency`. The template also contains:

`lifecycle_event_type`, `trade_time`, `settlement_date`, `position_effective_date`, `entitlement_date`, `acquisition_date`, `settlement_cash_account_id`, `instrument_id`, `quantity`, `price`, `counter_amount`, `fx_rate`, `fees`, `fee_category`, `taxes`, `counterparty_account_id`, `source_system`, `external_reference`, `note`.

Downloads add read-only `transaction_id`, `row_version`, `created_at`, and derived `option_action`. CSV preview validates the complete candidate history; import is all-or-nothing and requires the unchanged `preview_digest` returned by preview.

See [the mixed stock, fund, option, and FCN example](examples/transaction_import_stock_fund_option_fcn.csv). Database ownership and fields are documented in [PORTFOLIO_DATABASE_DICTIONARY.md](PORTFOLIO_DATABASE_DICTIONARY.md).
