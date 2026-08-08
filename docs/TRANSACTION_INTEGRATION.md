# Portfolio transaction integration

Status: implementation contract for migration heads `instrument_registry@20260807_0019` and `portfolio@20260807_0044`.

## Integration boundary

External projects must write through the Portfolio HTTP API. They must not insert into `portfolio.transaction_record` directly: the API owns account and Registry validation, source precision, idempotency, audit logs, position-history validation, event-group integrity, covered-call checks, and downstream snapshot invalidation.

Two supported write paths use the same transaction contract:

1. Single fact: `POST /api/portfolios/{portfolio_id}/transactions`.
2. Atomic CSV batch: preview with `POST /api/portfolios/{portfolio_id}/transactions/csv/preview`, then import with `POST /api/portfolios/{portfolio_id}/transactions/csv/import`.

Every write request should include a unique `Idempotency-Key` header. An upstream system should also populate `source_system` and `external_reference`; `(portfolio_id, source_system, external_reference)` is unique in the database.

## Event-valued instruments

Registry instrument types now include `equity`, `fcn`, and `option`.

- Stocks and ETFs use Registry market prices as before.
- Long FCN and option positions are carried at their remaining transaction cost between events. They use `valuation_basis=carried_cost`, `quote_basis=carried_cost`, `quote_status=event-cost`, a null quote identity, and a separate carrying-value field. They do not enter priced-market coverage.
- Event-valued purchases expense recorded fees and taxes immediately; those charges are not capitalized into carrying basis.
- A covered-call sell-to-open creates settlement cash and an equal premium-basis liability. Premium is recognized as realized option P&L only when that liability is released by close, expiry, or assignment.
- Option-writer records are explicit `option_obligation` holding rows, not negative long lots. They use `valuation_basis=premium_liability`, report open contracts and covered underlying units, and remain outside market-return and risk samples.
- `option_write` is permitted only while the related stock/ETF quantity covers all outstanding writer obligations. An underlying sale is rejected if it would leave an uncovered obligation.

The total balance sheet and NAV include carrying-basis assets and liabilities. Any total-portfolio return produced while a material event-valued position or obligation exists is an operational/carrying-basis return, not a complete fair-value or GIPS-informed TWR. Event-valued day change is `null`. Forward risk uses only rows selected by the effective-dated `risk_eligible` policy and discloses excluded carrying value, excluded liability, coverage, and excluded rows; it never treats an excluded derivative return as zero.

This is an event-accounting policy, not fair-value or statutory derivative accounting.

## Option contract identity and actions

Every Registry `option` requires all of the following fields. Identity-less option rows are rejected; migration `20260806_0018` intentionally stops if such rows already exist.

- underlying instrument ID
- option type (`call` or `put`)
- expiry date
- positive strike
- positive contract multiplier
- settlement type (`physical` or `cash`)
- contract currency equal to the instrument currency

The persisted transaction types remain unchanged, but every read response and CSV export includes one backend-derived canonical `option_action`:

| Persisted fact | Required instrument | Canonical action |
|---|---|---|
| `buy` | option | `buy_to_open` |
| `sell` | option | `sell_to_close` |
| `option_write` | physical call | `sell_to_open` |
| `option_buy_to_close` | physical call | `buy_to_close` |

Writer actions currently support covered physical calls only. Cash-secured puts, naked calls, naked puts, and incomplete contracts are rejected.

## Derivative Registry governance

Every Registry `fcn` also requires a complete `fcn_contract` with positive notional, issue and maturity dates, contract currency, issuer, counterparty, one or more underlying instrument IDs, one or more deliverable instrument IDs, and barrier type/level. Maturity cannot precede issue date, contract currency must equal instrument currency, references cannot point back to the FCN itself, and active barriers require a positive level.

Both `fcn` and `option` require a `corporate_action_adjustment_policy` with policy authority, quantity rounding, and explicit strike/multiplier/deliverable adjustment flags. This is contract governance, not an instruction for Portfolio to guess an adjustment. A lifecycle action continues to use the canonical Registry contract and must be reviewed or rejected when the governing adjustment has not been incorporated.

Broker identities are stored as typed `contract_id`, `symbol`, or `product_code` records. Each represented broker has exactly one primary identifier, and the broker/type/value tuple is globally unique. The Registry derives a deterministic canonical contract ID from the canonical derivative contract plus its adjustment policy. Reconciliation status is `ready` when broker keys exist and `unmatched` when the canonical contract is valid but no broker-specific identity has been registered; unmatched is visible operational state, not permission to match by display name.

Migration `20260807_0019` deliberately refuses an automatic upgrade when derivative rows already exist because FCN metadata and adjustment governance cannot be inferred safely. Its downgrade also refuses to discard populated broker identities or derivative governance metadata.

## Holdings operational read contract

`GET /api/workspace/holdings?portfolio_id={portfolio_id}` partitions rows into `market_valued_positions`, `structured_and_long_derivatives`, `written_option_obligations`, and `cash_and_settlement`. Written obligations expose required, covered, and uncovered underlying quantities, coverage ratio, expiry, strike, settlement type, assignment notional, remaining premium basis, and liability. Underlying coverage is allocated only once within each account/underlying pool, earliest expiry first.

Pending monetary rows retain settlement date, pending-until date, status, local amount, and base amount. Their stable identity includes both dates, so otherwise identical settlements remain distinct. Workspace operational output includes expiry buckets, uncovered and physical-assignment exposure, settlement receivable/payable/net, overdue and unavailable-FX counts, plus line-addressable alerts. A normal workspace still publishes an explicit uncovered count of zero.

The Regions CSV export has one fixed 24-column schema across all four regions and writes `N/A` for inapplicable fields. The Advanced Table export follows only its currently visible columns.

## Transaction and lifecycle values

| `transaction_type` | Purpose | Position effect | Cash effect |
|---|---|---:|---:|
| `buy` | Buy a stock, ETF, FCN, or long option | Opens/increases a long lot | `-(gross + fees + taxes)` |
| `sell` | Sell an existing long position | Reduces a long lot | `gross - fees - taxes` |
| `coupon` | FCN/bond coupon | None | `gross - fees - taxes` |
| `maturity_redemption` | Close FCN/bond/long-option quantity | Reduces a long lot | `gross - fees - taxes` |
| `lifecycle_event` | Non-cash lifecycle fact | None | `0` |
| `option_write` | Open/increase a covered-call obligation and premium liability | Obligation only | `gross - fees - taxes` |
| `option_buy_to_close` | Buy back a writer obligation and release allocated premium basis | Reduces obligation | `-(gross + fees + taxes)` |

Supported `lifecycle_event_type` values:

| Value | Required `transaction_type` | Meaning |
|---|---|---|
| `fcn_knock_in` | `lifecycle_event` | FCN knocked in; no cash or position change yet |
| `fcn_knock_out` | `maturity_redemption` | FCN closed by knock-out |
| `fcn_maturity` | `maturity_redemption` | FCN cash maturity |
| `fcn_physical_settlement` | `maturity_redemption` | FCN closed into delivered stock/ETF |
| `option_long_expiry` | `maturity_redemption` | Long option expires; `gross_amount=0` is allowed |
| `option_long_exercise` | `maturity_redemption` | Long option closes through exercise; `gross_amount=0` |
| `option_writer_expiry` | `lifecycle_event` | Writer obligation expires worthless |
| `option_assignment` | `lifecycle_event` | Writer is assigned; linked stock sale delivers shares |

`quantity` on long-option facts is the exchange contract count. `quantity` on writer facts is the covered underlying deliverable; it must be an integral multiple of the Registry contract multiplier. Responses expose both `open_contract_quantity` and `covered_underlying_quantity` for writer obligations.

## Physical settlement recipes

Each recipe is exactly two rows with one portfolio-unique `event_group_id`, submitted in one CSV import. The API validates and commits the group atomically; deleting either row deletes the complete group.

### FCN receives stock

1. Optional earlier `fcn_knock_in` lifecycle fact.
2. `maturity_redemption` on the FCN with `lifecycle_event_type=fcn_physical_settlement`, the delivered stock in `related_instrument_id`, and `gross_amount=0`.
3. `buy` on that stock with the same settlement date, settlement cash account, and event group. Its positive price/gross is the terminal settlement value of the delivered stock; it determines FCN realized P&L and the new stock lot basis but does not create a principal cash leg.

The ledger closes the FCN against the linked stock row's terminal settlement value. FCN realized P&L is `terminal settlement value - released FCN basis - FCN close-leg fees and taxes`; the delivered stock opens at that same terminal value, and only explicit fees and taxes move cash. A reliable EOD market quote then determines the stock's own unrealized P&L. Missing EOD price or FX makes valuation/performance unavailable instead of treating the terminal value as an EOD quote.

### Long option is exercised

1. `maturity_redemption` on the option with `lifecycle_event_type=option_long_exercise`, contract quantity, the underlying in `related_instrument_id`, `gross_amount=0`, and an event group.
2. A same-day `buy` of the underlying in that group. Delivered quantity must equal option contracts multiplied by the Registry multiplier, and its gross amount must equal the contract strike cash.

The option's actual remaining premium basis is transferred into the underlying lot. Underlying basis is `strike cash + released premium basis`; recorded fees and taxes remain current expenses. Option realized P&L is zero, while the underlying's reliable EOD quote determines unrealized P&L and total NAV/TWR.

### Covered call is assigned

1. Earlier `option_write` with the option in `instrument_id`, covered stock in `related_instrument_id`, and premium in `gross_amount`.
2. `lifecycle_event` with `lifecycle_event_type=option_assignment`, covered-unit `quantity`, the same option and related stock, and an assignment `event_group_id`.
3. A following `sell` of the same covered-unit quantity at the strike price, on the same trade date and in the same event group.

The assignment fact must sort before the stock sale, so the obligation closes before shares leave the account. CSV row order supplies the deterministic tie-break when trade time is the same.

Assignment releases the allocated premium liability into realized option P&L. The linked stock sale remains an ordinary sale at contract strike proceeds, so premium is not counted again in stock proceeds.

## Canonical CSV schema

Download a header-only template from `GET /api/portfolios/{portfolio_id}/transactions/csv-template`. Download a round-trippable export from `GET /api/portfolios/{portfolio_id}/transactions.csv`.

| Column | Required | Description |
|---|---:|---|
| `transaction_type` | yes | Economic transaction type |
| `lifecycle_event_type` | conditional | Structured FCN/option lifecycle |
| `trade_date` | yes | Execution/event date, `YYYY-MM-DD` |
| `trade_time` | no | Local `HH:MM`; blank uses the configured estimated time |
| `settlement_date` | no | Cash settlement date; defaults to trade date |
| `position_effective_date` | no | Independent long-position recognition date |
| `entitlement_date` | no | Income entitlement date |
| `acquisition_date` | no | Historical acquisition date for opening balances |
| `account_id` | yes | Portfolio account owning the fact |
| `settlement_cash_account_id` | conditional | Deposit account used by cash-settled security facts |
| `instrument_id` | conditional | Canonical Registry instrument |
| `related_instrument_id` | conditional | Underlying/delivered equity or ETF |
| `event_group_id` | conditional | Reserved for exactly one physical lifecycle fact and its linked buy/sell |
| `quantity` | conditional | Long units or covered underlying units |
| `price` | conditional | Per-unit traded price; optional for option-writer facts |
| `gross_amount` | yes | Non-negative local-currency amount; zero for non-cash lifecycle facts |
| `counter_amount` | FX only | FX target amount |
| `fx_rate` | FX only | Target amount divided by source amount |
| `fees` | no | Defaults to zero |
| `fee_category` | no | Defaults to `unknown` |
| `taxes` | no | Defaults to zero |
| `currency` | yes | `USD`, `HKD`, or `CNY` |
| `counterparty_account_id` | FX/transfer | Paired account when applicable |
| `source_system` | recommended | Stable upstream system name |
| `external_reference` | recommended | Stable upstream fact/order identifier |
| `note` | no | Free-text audit note |

Exports add read-only `transaction_id`, `row_version`, `created_at`, and derived `option_action`. Source decimal values are exported instead of float projections so a download/import round trip preserves recorded precision. CSV cells that could execute as spreadsheet formulas are escaped.

## Preview and import

Preview request:

```json
{
  "csv_text": "transaction_type,...",
  "default_source_system": "colleague_project"
}
```

The response contains normalized rows, row errors, batch errors, warnings, and `preview_digest`. Preview checks the full candidate history, including available long positions, option-writer obligations, covered quantity, and physical event groups.

Import the unchanged text with the returned digest:

```http
POST /api/portfolios/{portfolio_id}/transactions/csv/import
Idempotency-Key: colleague-project-import-20260804-001
Content-Type: application/json
```

```json
{
  "csv_text": "transaction_type,...",
  "default_source_system": "colleague_project",
  "preview_digest": "<64-character sha256>"
}
```

The import is all-or-nothing. A changed file, duplicate source identity, invalid event group, insufficient position, or uncovered call rejects the whole batch.

Portfolio migration `20260807_0044` adds a database-backed transaction sequence, effective-dated analytics scope policies and taxonomy selections, point-in-time taxonomy configuration revisions, and Research simulation assumptions. Downgrade fails closed while any of those new business facts exist. Materialized snapshots include scope policy identity and are rebuilt rather than read through an old payload compatibility path.

See [`examples/transaction_import_fcn_covered_call.csv`](examples/transaction_import_fcn_covered_call.csv) for a complete FCN-to-stock-to-covered-call sequence.
