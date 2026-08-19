# Portfolio transaction integration

External projects write transactions through the Portfolio HTTP API, not directly to database tables. The API applies the same validation, audit, idempotency, and snapshot invalidation rules to manual entry, direct API writes, and CSV/Excel imports.

Accounts are first-class transaction routing facts. Create `Cash`, `Security`, `FCN`, and `Option` accounts separately; each holding account must point to a same-currency Cash account. A Registry security may use only a Security account, an FCN contract only an FCN account, and an option contract only an Option account. The API does not accept the removed instrument-scope field or a mixed holding account.

## Write paths

- Single fact: `POST /api/portfolios/{portfolio_id}/transactions`
- CSV or Excel file preview: `POST /api/portfolios/{portfolio_id}/transactions/files/preview`
- CSV or Excel file import: `POST /api/portfolios/{portfolio_id}/transactions/files/import`
- Blank templates: `GET /api/portfolios/{portfolio_id}/transactions/csv-template` and `.../xlsx-template`
- Importable all-transaction exports: `GET /api/portfolios/{portfolio_id}/transactions.csv` and `.../transactions.xlsx`

The original JSON-body CSV preview/import endpoints remain the integration contract for systems that already send CSV text. The workspace itself uses the multipart file endpoints so CSV and Excel go through one file parser and the same portfolio validation path.

Send a unique `Idempotency-Key` header with writes. An upstream project should also send `source_system` and a stable `external_reference`; that pair is unique inside a portfolio.

Example: sell three Put contracts to open, with a multiplier of 100:

```json
{
  "transaction_type": "option_write",
  "trade_date": "2026-02-15",
  "settlement_date": "2026-02-15",
  "account_id": "broker-us-option",
  "settlement_cash_account_id": "cash-usd-main",
  "derivative_contract_id": "option-demo-put-001",
  "derivative_contract": {
    "derivative_contract_id": "option-demo-put-001",
    "contract_name": "Demo Dec 45 Put",
    "contract_type": "option",
    "external_reference": "BROKER-PUT-001",
    "terms": {
      "underlying_instrument_id": "equity-demo-001",
      "option_type": "put",
      "expiry_date": "2026-12-18",
      "strike": 45,
      "contract_multiplier": 100
    }
  },
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

The entry workflow chooses the asset first: `Security`, `FCN`, `Option`, or `Cash & Operations`. The API derives the same persisted domain on every returned transaction, and filters and exports preserve it. Portfolio does not create or bind multi-leg derivative transactions, and the transaction schema has no derivative relation or event-group field. If two rows are economically related, record both independently and explain the relationship in `note` when useful. Market securities use Registry `instrument_id`; an FCN or option uses a Portfolio-local `derivative_contract_id`.

Examples:

- An option that expires worthless is closed with the appropriate zero-cash expiry event.
- An option settled for cash is closed with the appropriate cash-settlement event and its actual settlement amount.
- If the broker physically delivers stock, normalize the economics into an option cash settlement plus an independent ordinary stock trade at the delivery-date market/reference price. For example, a physically exercised long Call is represented by a long-option cash receipt for intrinsic value plus a stock `buy` at market price; together they reproduce the strike-price purchase economics.
- FCN knock-in with asset delivery: close the FCN with result `fcn_knock_in`, then enter a separate asset `buy`.

The system does not require matching quantities, timestamps, references, or notes between those rows.

## Option facts

An option contract is created inside its Portfolio, atomically with its first transaction. The inline contract records Call/Put type, Registry underlying, expiry, strike and multiplier. It does not predeclare a settlement mode. Later transactions reference only the same `derivative_contract_id`; the contract terms are not copied into Registry or repeated on every row.

| Stored `transaction_type` | Option action | Quantity |
|---|---|---|
| `buy` | buy to open | contracts |
| `sell` | sell to close | contracts |
| `option_write` | sell to open | contracts |
| `option_buy_to_close` | buy to close | contracts |
| `maturity_redemption` + `option_long_expiry` | close expired long option | contracts |
| `maturity_redemption` + `option_long_cash_settlement` | cash-settle long option | contracts |
| `lifecycle_event` + `option_writer_expiry` | close expired short option | contracts |
| `lifecycle_event` + `option_writer_cash_settlement` | cash-settle short option | contracts |

Expiry events require zero gross amount, zero fees/taxes, and no settlement cash account. Cash-settlement events require a positive gross amount and a settlement cash account; long settlement is a cash inflow and writer settlement is a cash outflow. Portfolio intentionally has no physical-delivery event or technical link to the independent stock trade.

Database revision `20260810_0049` deliberately refuses to upgrade while legacy `option_long_exercise`, `option_assignment`, or generic option-closing facts remain. Remove those ambiguous rows before migration; after the new lifecycle values are available, recreate the reviewed cash-settlement facts and any independent stock trades. The migration does not guess cash amounts, stock trades, or settlement intent. After the preflight succeeds it removes `settlement_type` from every option contract and installs the new lifecycle constraint. Downgrade is intentionally unsupported; rollback uses the pre-upgrade database backup.

Both Call and Put contracts, long and short, are supported. For premium trades, `price` is premium per underlying unit and:

`gross_amount = contract quantity × premium price × Portfolio contract multiplier`

Long options remain at transaction cost between recorded events. Short options appear as negative contract positions with a remaining premium-basis liability. No daily option market price or covered-position test is required.

## FCN facts

| Stored fact | Meaning |
|---|---|
| `buy` | enter/buy the FCN contract |
| `sell` | exit/sell the FCN contract before maturity |
| `coupon` | FCN interest income |
| `maturity_redemption` + blank/`fcn_maturity` | close normally |
| `maturity_redemption` + `fcn_knock_in` | close with knock-in result |
| `maturity_redemption` + `fcn_knock_out` | close with knock-out result |

FCNs remain at transaction cost between recorded events. Portfolio does not verify barrier levels and does not infer or bind any delivered asset.

The first FCN transaction creates a Portfolio-local contract. Master terms contain notional, optional annual coupon rate, issue/final-observation/maturity dates, issuer, and counterparty. The `underlyings` array records each Registry security with optional initial reference price, strike/knock-in/knock-out levels in percentage points, and a deliverable flag. These are recorded terms, not a live valuation model or automatic barrier monitor. Revision `20260816_0051` converts the former shared barrier field to per-underlying terms and deliberately rejects ambiguous legacy `dual` barriers instead of guessing two levels.

## Transaction files

The Transactions workspace uses one canonical schema in both CSV and Excel. Excel workbooks use a worksheet named `Transactions`; formulas and legacy `.xls` files are rejected. The populated export and blank template use the same columns in both formats.

| Action | Scope | Result |
|---|---|---|
| Export | every current transaction in the portfolio, independent of current filters | populated canonical CSV or Excel file accepted by Import |
| CSV template | canonical headers with no rows | machine-oriented starting point for batch generation |
| Guided Excel template | blank canonical `Transactions` sheet plus instructions, field guide, examples, and enum lists | starting point for manual entry by collaborators; only `Transactions` is imported |
| Import | either format after review or editing | atomic preview and batch creation through the same validation path |

The file represents transaction commands, not database rows. It therefore excludes transaction IDs,
row versions, change history, resolved timestamps, internal accounting types, and internal pair IDs.
Every row starts with `asset_type` (`security`, `fcn`, `option`, or `cash`) and a
`transaction_action` allowed for that asset. FCN and Option close outcomes are selected directly as
asset actions; file authors never choose lifecycle or maturity accounting types. One internal
transfer is entered once as `transfer_out` or `transfer_in`, using `account_id` for the selected side
and `counterparty_account_id` for the other side. Import regenerates the atomic persisted pair.

Required columns are `asset_type`, `transaction_action`, `trade_date`, `account_id`,
`gross_amount`, and `currency`. The template also contains:

`trade_time`, `settlement_date`, `position_effective_date`, `entitlement_date`, `acquisition_date`,
`counterparty_account_id`, `settlement_cash_account_id`, `instrument_id`,
`derivative_contract_id`, derivative definition/term columns, `quantity`, `price`, `counter_amount`,
`fx_rate`, `fees`, `fee_category`, `taxes`, `source_system`, `external_reference`, and `note`.

For a new derivative contract, place its definition on the first file row. FCN rows use `fcn_annual_coupon_rate_pct`, `fcn_final_observation_date`, and `fcn_underlyings_json` for the per-underlying term array. Later rows leave the definition columns blank and keep only `derivative_contract_id`. A row may use `instrument_id` or `derivative_contract_id`, never both.

The guided Excel workbook contains five sheets: `Instructions`, blank `Transactions`, `Field Guide`, `Examples`, and `Lists`. Dependent dropdowns first select the asset and then show only that asset's actions. Structural validation also covers fee categories, USD/HKD/CNY, option types, dates, times, and non-negative numbers. It intentionally contains no portfolio, account, instrument, or existing-contract data. All IDs in `Examples` are placeholders and must be replaced with IDs that already exist in the target portfolio.

Supported transaction currencies are exactly `USD`, `HKD`, and `CNY`. A holding account, its security or derivative contract, the settlement cash account, and the transaction must be currency-compatible. Cash internal transfers are same-currency only; position transfers require the instrument and both security accounts to use the same currency. For FX conversion, `currency` is the source cash-account currency, the target currency comes from `counterparty_account_id`, and `counter_amount = gross_amount * fx_rate`.

The examples cover every action for Cash, Security, FCN, and Option, including both transfer directions. Physical option exercise or assignment remains an Option cash-settlement action plus an independent underlying Security action. FCN asset delivery remains an FCN close action plus an independent Security `buy`; neither workflow creates a pairing or relation ID. `opening_balance` represents an existing long option position. An existing written position must be backfilled with `sell_to_open` using its actual open date, quantity, and premium; it must not be represented as a positive-quantity opening balance.

File preview validates the complete candidate history against the target portfolio's accounts,
instruments, contracts, currencies, and existing positions. Import is all-or-nothing and requires the
unchanged `preview_digest` returned by preview. Database backup and transaction change history remain
separate operational concerns rather than a second transaction-file format.

The example expects `broker-us-core`, `broker-us-option`, and `broker-us-fcn` to exist as same-currency Security, Option, and FCN accounts before preview. See [the mixed stock, fund, option, and FCN example](examples/transaction_import_stock_fund_option_fcn.csv). Database ownership and fields are documented in [PORTFOLIO_DATABASE_DICTIONARY.md](PORTFOLIO_DATABASE_DICTIONARY.md).
