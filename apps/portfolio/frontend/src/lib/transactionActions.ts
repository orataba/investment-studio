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
          action('buy', fund ? 'Subscription' : 'Buy', 'buy'),
          action('sell', fund ? 'Redemption' : 'Sell', 'sell'),
          ...(['equity', 'etf'].includes(assetSubtype ?? '') ? [action('short_sell', 'Short Sell', 'short_sell'), action('buy_to_cover', 'Buy to Cover', 'buy_to_cover')] : []),
        ],
      },
      {
        label: 'Income and capital',
        actions: [
          action('dividend', 'Dividend', 'dividend'),
          action(
            'dividend_reinvestment',
            'Dividend Reinvestment',
            'dividend_reinvestment',
          ),
          action('return_of_capital', 'Return of Capital', 'return_of_capital'),
        ],
      },
      {
        label: 'Charges',
        actions: [
          action('fee', 'Fee', 'fee'),
          action('tax', 'Tax', 'tax'),
        ],
      },
      {
        label: 'Transfers and setup',
        actions: [
          action('transfer_out', 'Transfer Out', 'transfer_out', null, 'position'),
          action('transfer_in', 'Transfer In', 'transfer_in', null, 'position'),
          action('opening_balance', 'Opening Balance', 'opening_balance'),
          ...(['equity', 'etf'].includes(assetSubtype ?? '') ? [action('short_opening_balance', 'Short Stock Opening Balance', 'short_opening_balance')] : []),
        ],
      },
    ]
  }

  if (assetDomain === 'cash') {
    return [
      {
        label: 'Cash movements',
        actions: [
          action('deposit', 'Deposit', 'deposit'),
          action('withdrawal', 'Withdrawal', 'withdrawal'),
          action('interest', 'Interest', 'interest'),
          action('fx_conversion', 'FX Conversion', 'fx_conversion'),
        ],
      },
      {
        label: 'Charges',
        actions: [
          action('fee', 'Fee', 'fee'),
          action('tax', 'Tax', 'tax'),
        ],
      },
      {
        label: 'Transfers and setup',
        actions: [
          action('transfer_out', 'Transfer Out', 'transfer_out', null, 'cash'),
          action('transfer_in', 'Transfer In', 'transfer_in', null, 'cash'),
          action('opening_balance', 'Opening Balance', 'opening_balance'),
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
            action('entry', 'FCN Entry', 'buy'),
            action('opening_balance', 'FCN Opening Balance', 'opening_balance'),
          ],
        },
      ]
    }
    return [
      {
        label: 'Contract activity',
        actions: [
          action('entry', 'FCN Entry', 'buy'),
          action('early_exit', 'FCN Early Exit', 'sell'),
          action('coupon', 'FCN Coupon', 'coupon'),
          action('knock_in_observation', 'FCN Knock-In Observation', 'lifecycle_event', 'fcn_knock_in'),
        ],
      },
      {
        label: 'Contract close outcomes',
        actions: [
          action('knock_in_close', 'FCN Knock-In Close', 'maturity_redemption', 'fcn_knock_in'),
          action('knock_out_close', 'FCN Knock-Out Close', 'maturity_redemption', 'fcn_knock_out'),
          action('maturity_close', 'FCN Maturity Close', 'maturity_redemption', 'fcn_maturity'),
        ],
      },
      {
        label: 'Charges and setup',
        actions: [
          action('fee', 'FCN Fee', 'fee'),
          action('tax', 'FCN Tax', 'tax'),
          action('opening_balance', 'FCN Opening Balance', 'opening_balance'),
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
          action('buy_to_open', `Buy to Open ${name}`, 'buy'),
          action('sell_to_open', `Sell to Open ${name}`, 'option_write'),
          action('opening_balance', 'Long Option Opening Balance', 'opening_balance'),
          action('opening_written', 'Written Option Opening Balance', 'option_opening_balance'),
        ],
      },
    ]
  }
  return [
    {
      label: 'Open and close',
      actions: [
        action('buy_to_open', `Buy to Open ${name}`, 'buy'),
        action('sell_to_close', `Sell to Close ${name}`, 'sell'),
        action('sell_to_open', `Sell to Open ${name}`, 'option_write'),
        action('buy_to_close', `Buy to Close ${name}`, 'option_buy_to_close'),
      ],
    },
    {
      label: 'Long outcomes',
      actions: [
        action(
          'exercise_long',
          `Exercise Long ${name}`,
          'maturity_redemption',
          'option_long_exercise',
        ),
        action(
          'expire_long',
          `Expire Long ${name}`,
          'maturity_redemption',
          'option_long_expiry',
        ),
        action(
          'cash_settle_long',
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
          'assign_written',
          `Assign Written ${name}`,
          'lifecycle_event',
          'option_writer_assignment',
        ),
        action(
          'expire_written',
          `Expire Written ${name}`,
          'lifecycle_event',
          'option_writer_expiry',
        ),
        action(
          'cash_settle_written',
          `Cash-Settle Written ${name}`,
          'lifecycle_event',
          'option_writer_cash_settlement',
        ),
      ],
    },
    {
      label: 'Charges and setup',
      actions: [
        action('fee', 'Option Fee', 'fee'),
        action('tax', 'Option Tax', 'tax'),
        action('opening_balance', 'Long Option Opening Balance', 'opening_balance'),
        action('opening_written', 'Written Option Opening Balance', 'option_opening_balance'),
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
