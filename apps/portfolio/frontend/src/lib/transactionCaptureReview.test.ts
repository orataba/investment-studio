import { describe, expect, it } from 'vitest'

import type {
  PortfolioTransactionCaptureAnalysisRevision,
  PortfolioTransactionImportRequest,
} from './api'
import {
  buildHumanReviewedCaptureProposal,
  changeTransactionCaptureAction,
  type TransactionCaptureReviewDraft,
} from './transactionCaptureReview'

it('moves delivery charges to the stock leg and removes delivery details when correcting the action back', () => {
  const draft = { ...transactionImport.records[0], asset_type: 'option' as const,
    transaction_action: 'cash_settle_long' as const, gross_amount: '500', fees: '3', fee_category: 'transaction_cost' as const,
    settlement_cash_account_id: 'cash-1', taxes: '1' }
  const physical = changeTransactionCaptureAction(draft, 'physical_long')
  expect(physical).toMatchObject({ gross_amount: 0, fees: 0, fee_category: 'unknown', taxes: 0, settlement_cash_account_id: null,
    option_delivery: { stock_account_id: '', settlement_cash_account_id: 'cash-1', fees: '3', fee_category: 'transaction_cost', taxes: '1' } })
  const corrected = changeTransactionCaptureAction(physical, 'cash_settle_long')
  expect(corrected.option_delivery).toBeUndefined()
  expect(corrected).toMatchObject({ settlement_cash_account_id: 'cash-1', fees: '3', fee_category: 'transaction_cost', taxes: '1' })
})


const transactionImport: PortfolioTransactionImportRequest = {
  source_system: 'portfolio_screenshot_assistant',
  records: [
    {
      external_reference: 'batch-1#1',
      asset_type: 'security',
      transaction_action: 'buy',
      trade_date: '2026-08-24',
      account_id: 'account-a',
      instrument_id: 'instrument-a',
      quantity: '10',
      price: '10',
      gross_amount: '100',
      currency: 'USD',
    },
    {
      external_reference: 'batch-1#2',
      asset_type: 'security',
      transaction_action: 'buy',
      trade_date: '2026-08-25',
      account_id: 'account-b',
      instrument_id: 'instrument-b',
      quantity: '20',
      price: '10',
      gross_amount: '200',
      currency: 'USD',
    },
  ],
}

const analysis = {
  batch_id: 'batch-1',
  revision: 1,
  source: 'assistant',
  harness: 'deepseek-harness',
  provider: 'deepseek',
  model_name: 'vision-model',
  schema_version: 'portfolio.transaction-capture-analysis.v2',
  analysis: {
    summary: 'Two possible trades.',
    documents: [{ capture_id: 'capture-1', document_kind: 'trade_activity' }],
    candidates: [
      {
        candidate_id: 'candidate-1',
        candidate_kind: 'transaction',
        account_resolution: {
          status: 'resolved',
          account_id: 'account-a',
          candidate_account_ids: ['account-a'],
        },
        fields: [],
        proposed_transaction_record_index: 1,
        possible_existing_transaction_ids: ['transaction-existing'],
        duplicate_assessment: 'uncertain',
      },
      {
        candidate_id: 'candidate-2',
        candidate_kind: 'transaction',
        account_resolution: {
          status: 'resolved',
          account_id: 'account-a',
          candidate_account_ids: ['account-a'],
        },
        fields: [],
        proposed_transaction_record_index: 2,
        duplicate_assessment: 'not_assessed',
      },
    ],
    questions: ['Confirm the possible duplicate.'],
  },
  transaction_import: transactionImport,
  created_at: '2026-08-26T00:00:00Z',
} satisfies PortfolioTransactionCaptureAnalysisRevision


describe('buildHumanReviewedCaptureProposal', () => {
  it('removes unresolved duplicates and reindexes the retained source identities', () => {
    const draft: TransactionCaptureReviewDraft = {
      batchId: 'batch-1',
      sourceRevision: 1,
      transactionImport,
      duplicateAssessments: { 'candidate-1': 'same_record' },
    }

    const reviewed = buildHumanReviewedCaptureProposal('batch-1', analysis, draft)

    expect(reviewed.transactionImport?.records).toHaveLength(1)
    expect(reviewed.transactionImport?.records[0]).toMatchObject({
      external_reference: 'batch-1#1',
      account_id: 'account-b',
      instrument_id: 'instrument-b',
    })
    expect(reviewed.analysis.questions).toEqual([])
    expect(reviewed.analysis.candidates[0]).toMatchObject({
      proposed_transaction_record_index: null,
      duplicate_assessment: 'same_record',
    })
    expect(reviewed.analysis.candidates[1]).toMatchObject({
      proposed_transaction_record_index: 1,
      duplicate_assessment: 'not_assessed',
      account_resolution: {
        status: 'resolved',
        account_id: 'account-b',
        candidate_account_ids: ['account-a', 'account-b'],
      },
    })
  })

  it('keeps an explicitly distinct candidate in Preview', () => {
    const draft: TransactionCaptureReviewDraft = {
      batchId: 'batch-1',
      sourceRevision: 1,
      transactionImport,
      duplicateAssessments: { 'candidate-1': 'distinct_records' },
    }

    const reviewed = buildHumanReviewedCaptureProposal('batch-1', analysis, draft)

    expect(reviewed.transactionImport?.records.map((record) => record.external_reference)).toEqual([
      'batch-1#1',
      'batch-1#2',
    ])
    expect(reviewed.analysis.candidates[0]).toMatchObject({
      proposed_transaction_record_index: 1,
      duplicate_assessment: 'distinct_records',
    })
  })
})
