export type TransactionAssetDomain = 'security' | 'derivative' | 'cash'
export type TransactionDerivativeSubtype = 'option' | 'fcn'

export type TransactionActionContext = {
  newDerivativeContract?: boolean
}

export type TransactionAction = {
  value: string
  label: string
  transactionType: string
  lifecycleEventType: string | null
  transferObjectType?: 'cash' | 'position'
}

export type TransactionActionGroup = {
  label: string
  actions: TransactionAction[]
}

function action(
  value: string,
  label: string,
  transactionType: string,
  lifecycleEventType: string | null = null,
  transferObjectType?: 'cash' | 'position',
): TransactionAction {
  return { value, label, transactionType, lifecycleEventType, transferObjectType }
}

function optionName(optionType?: 'call' | 'put' | null) {
  if (optionType === 'call') return 'Call'
  if (optionType === 'put') return 'Put'
  return 'Option'
}

export function transactionActionGroups(
  assetDomain: TransactionAssetDomain,
  assetSubtype?: string | null,
  optionType?: 'call' | 'put' | null,
  context: TransactionActionContext = {},
): TransactionActionGroup[] {
  if (assetDomain === 'security') {
    const fund = assetSubtype === 'public_fund' || assetSubtype === 'private_fund'
    return [
      {
        label: 'Trades',
        actions: [
          action('security_buy', fund ? 'Subscription' : 'Buy', 'buy'),
          action('security_sell', fund ? 'Redemption' : 'Sell', 'sell'),
        ],
      },
      {
        label: 'Income and capital',
        actions: [
          action('security_dividend', 'Dividend', 'dividend'),
          action(
            'security_dividend_reinvestment',
            'Dividend Reinvestment',
            'dividend_reinvestment',
          ),
          action('security_return_of_capital', 'Return of Capital', 'return_of_capital'),
        ],
      },
      {
        label: 'Charges',
        actions: [
          action('security_fee', 'Fee', 'fee'),
          action('security_tax', 'Tax', 'tax'),
        ],
      },
      {
        label: 'Transfers and setup',
        actions: [
          action('security_transfer_out', 'Transfer Out', 'transfer_out', null, 'position'),
          action('security_transfer_in', 'Transfer In', 'transfer_in', null, 'position'),
          action('security_opening_balance', 'Opening Balance', 'opening_balance'),
        ],
      },
    ]
  }

  if (assetDomain === 'cash') {
    return [
      {
        label: 'Cash movements',
        actions: [
          action('cash_deposit', 'Deposit', 'deposit'),
          action('cash_withdrawal', 'Withdrawal', 'withdrawal'),
          action('cash_interest', 'Interest', 'interest'),
          action('cash_fx_conversion', 'FX Conversion', 'fx_conversion'),
        ],
      },
      {
        label: 'Charges',
        actions: [
          action('cash_fee', 'Fee', 'fee'),
          action('cash_tax', 'Tax', 'tax'),
        ],
      },
      {
        label: 'Transfers and setup',
        actions: [
          action('cash_transfer_out', 'Transfer Out', 'transfer_out', null, 'cash'),
          action('cash_transfer_in', 'Transfer In', 'transfer_in', null, 'cash'),
          action('cash_opening_balance', 'Opening Balance', 'opening_balance'),
        ],
      },
    ]
  }

  if (assetSubtype === 'fcn') {
    if (context.newDerivativeContract) {
      return [
        {
          label: 'Create position',
          actions: [
            action('fcn_entry', 'FCN Entry', 'buy'),
            action('fcn_opening_balance', 'FCN Opening Balance', 'opening_balance'),
          ],
        },
      ]
    }
    return [
      {
        label: 'Contract activity',
        actions: [
          action('fcn_entry', 'FCN Entry', 'buy'),
          action('fcn_early_exit', 'FCN Early Exit', 'sell'),
          action('fcn_coupon', 'FCN Coupon', 'coupon'),
        ],
      },
      {
        label: 'Lifecycle outcomes',
        actions: [
          action('fcn_knock_in', 'FCN Knock-In Close', 'maturity_redemption', 'fcn_knock_in'),
          action('fcn_knock_out', 'FCN Knock-Out Close', 'maturity_redemption', 'fcn_knock_out'),
          action('fcn_maturity', 'FCN Maturity Close', 'maturity_redemption', 'fcn_maturity'),
        ],
      },
      {
        label: 'Charges and setup',
        actions: [
          action('fcn_fee', 'FCN Fee', 'fee'),
          action('fcn_tax', 'FCN Tax', 'tax'),
          action('fcn_opening_balance', 'FCN Opening Balance', 'opening_balance'),
        ],
      },
    ]
  }

  const name = optionName(optionType)
  if (context.newDerivativeContract) {
    return [
      {
        label: 'Open position',
        actions: [
          action('option_buy_to_open', `Buy to Open ${name}`, 'buy'),
          action('option_sell_to_open', `Sell to Open ${name}`, 'option_write'),
          action('option_opening_balance', 'Option Opening Balance', 'opening_balance'),
        ],
      },
    ]
  }
  return [
    {
      label: 'Open and close',
      actions: [
        action('option_buy_to_open', `Buy to Open ${name}`, 'buy'),
        action('option_sell_to_close', `Sell to Close ${name}`, 'sell'),
        action('option_sell_to_open', `Sell to Open ${name}`, 'option_write'),
        action('option_buy_to_close', `Buy to Close ${name}`, 'option_buy_to_close'),
      ],
    },
    {
      label: 'Long outcomes',
      actions: [
        action(
          'option_long_expiry',
          `Expire Long ${name}`,
          'maturity_redemption',
          'option_long_expiry',
        ),
        action(
          'option_long_cash_settlement',
          `Cash-Settle Long ${name}`,
          'maturity_redemption',
          'option_long_cash_settlement',
        ),
      ],
    },
    {
      label: 'Written outcomes',
      actions: [
        action(
          'option_writer_expiry',
          `Expire Written ${name}`,
          'lifecycle_event',
          'option_writer_expiry',
        ),
        action(
          'option_writer_cash_settlement',
          `Cash-Settle Written ${name}`,
          'lifecycle_event',
          'option_writer_cash_settlement',
        ),
      ],
    },
    {
      label: 'Charges and setup',
      actions: [
        action('option_fee', 'Option Fee', 'fee'),
        action('option_tax', 'Option Tax', 'tax'),
        action('option_opening_balance', 'Option Opening Balance', 'opening_balance'),
      ],
    },
  ]
}

export function resolveTransactionAction(
  groups: TransactionActionGroup[],
  value: string,
): TransactionAction | null {
  for (const group of groups) {
    const resolved = group.actions.find((candidate) => candidate.value === value)
    if (resolved) return resolved
  }
  return null
}

export function transactionActionValue(
  groups: TransactionActionGroup[],
  transactionType: string,
  lifecycleEventType?: string | null,
  transferObjectType?: string | null,
) {
  const resolved = groups
    .flatMap((group) => group.actions)
    .find(
      (candidate) =>
        candidate.transactionType === transactionType &&
        candidate.lifecycleEventType === (lifecycleEventType || null) &&
        (!candidate.transferObjectType || candidate.transferObjectType === transferObjectType),
    )
  return resolved?.value ?? groups[0]?.actions[0]?.value ?? ''
}
