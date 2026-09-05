import type {
  PortfolioTransactionCaptureAnalysisRevision,
  PortfolioTransactionImportAction,
  PortfolioTransactionImportCommand,
  PortfolioTransactionImportRequest,
} from './api'

export function changeTransactionCaptureAction(
  record: PortfolioTransactionImportCommand,
  action: PortfolioTransactionImportAction,
): PortfolioTransactionImportCommand {
  const { option_delivery: delivery, ...previous } = record
  if (action === 'physical_long' || action === 'physical_written') {
    return {
      ...record,
      transaction_action: action,
      gross_amount: 0,
      price: null,
      fees: 0,
      fee_category: 'unknown',
      taxes: 0,
      settlement_cash_account_id: null,
      option_delivery: delivery ?? {
        stock_account_id: '',
        settlement_cash_account_id: record.settlement_cash_account_id ?? '',
        fees: record.fees ?? 0,
        fee_category: record.fee_category ?? 'unknown',
        taxes: record.taxes ?? 0,
      },
    }
  }
  return {
    ...previous,
    transaction_action: action,
    ...(delivery ? {
      settlement_cash_account_id: delivery.settlement_cash_account_id,
      fees: delivery.fees ?? 0,
      fee_category: delivery.fee_category ?? 'unknown',
      taxes: delivery.taxes ?? 0,
    } : {}),
  }
}


export type TransactionCaptureDuplicateAssessment =
  | 'same_record'
  | 'distinct_records'
  | 'uncertain'

export type TransactionCaptureReviewDraft = {
  batchId: string
  sourceRevision: number
  transactionImport: PortfolioTransactionImportRequest
  duplicateAssessments: Record<string, TransactionCaptureDuplicateAssessment>
}

export function cloneTransactionImport(
  transactionImport: PortfolioTransactionImportRequest,
): PortfolioTransactionImportRequest {
  return JSON.parse(JSON.stringify(transactionImport)) as PortfolioTransactionImportRequest
}

export function hasTransactionCaptureImport(
  analysis: PortfolioTransactionCaptureAnalysisRevision | null | undefined,
): analysis is PortfolioTransactionCaptureAnalysisRevision & {
  transaction_import: PortfolioTransactionImportRequest
} {
  return Boolean(
    analysis?.transaction_import
    && Array.isArray(analysis.transaction_import.records)
    && analysis.transaction_import.records.length,
  )
}

export function transactionCaptureProposalReady(
  analysis: PortfolioTransactionCaptureAnalysisRevision | null | undefined,
) {
  return Boolean(
    hasTransactionCaptureImport(analysis)
    && analysis.source === 'human'
    && analysis.analysis.questions.length === 0
    && analysis.preview_digest
    && analysis.preview_error_count === 0,
  )
}

export function buildHumanReviewedCaptureProposal(
  batchId: string,
  analysis: PortfolioTransactionCaptureAnalysisRevision,
  draft: TransactionCaptureReviewDraft,
) {
  const candidateByRecordIndex = new Map(
    analysis.analysis.candidates
      .filter((candidate) => candidate.proposed_transaction_record_index)
      .map((candidate) => [candidate.proposed_transaction_record_index as number, candidate]),
  )
  const retainedRecordIndexes = draft.transactionImport.records
    .map((_record, recordIndex) => recordIndex + 1)
    .filter((recordIndex) => {
      const candidate = candidateByRecordIndex.get(recordIndex)
      if (
        !candidate
        || (!candidate.possible_duplicate_of?.length
          && !candidate.possible_existing_transaction_ids?.length)
      ) {
        return true
      }
      const assessment = draft.duplicateAssessments[candidate.candidate_id]
        ?? candidate.duplicate_assessment
        ?? 'uncertain'
      return assessment === 'distinct_records'
    })
  const remappedRecordIndexes = new Map(
    retainedRecordIndexes.map((recordIndex, retainedIndex) => [recordIndex, retainedIndex + 1]),
  )
  const reviewedRecords = retainedRecordIndexes.map((recordIndex, retainedIndex) => ({
    ...draft.transactionImport.records[recordIndex - 1],
    external_reference: `${batchId}#${retainedIndex + 1}`,
  }))
  const reviewedCandidates = analysis.analysis.candidates.map((candidate) => {
    const recordIndex = candidate.proposed_transaction_record_index
    const hasDuplicateReferences = Boolean(
      candidate.possible_duplicate_of?.length
      || candidate.possible_existing_transaction_ids?.length,
    )
    const duplicateAssessment: 'not_assessed' | TransactionCaptureDuplicateAssessment = hasDuplicateReferences
      ? draft.duplicateAssessments[candidate.candidate_id]
        ?? candidate.duplicate_assessment
        ?? 'uncertain'
      : 'not_assessed'
    if (!recordIndex) {
      return { ...candidate, duplicate_assessment: duplicateAssessment }
    }
    const remappedRecordIndex = remappedRecordIndexes.get(recordIndex)
    if (!remappedRecordIndex) {
      return {
        ...candidate,
        proposed_transaction_record_index: null,
        duplicate_assessment: duplicateAssessment,
      }
    }
    const reviewedRecord = reviewedRecords[remappedRecordIndex - 1]
    const existingResolution = candidate.account_resolution
    return {
      ...candidate,
      proposed_transaction_record_index: remappedRecordIndex,
      duplicate_assessment: duplicateAssessment,
      account_resolution: {
        ...(existingResolution ?? {}),
        status: 'resolved' as const,
        account_id: reviewedRecord.account_id,
        candidate_account_ids: Array.from(new Set([
          ...(existingResolution?.candidate_account_ids ?? []),
          reviewedRecord.account_id,
        ])),
      },
    }
  })

  return {
    analysis: {
      ...analysis.analysis,
      candidates: reviewedCandidates,
      questions: [],
    },
    transactionImport: reviewedRecords.length
      ? { ...draft.transactionImport, records: reviewedRecords }
      : null,
  }
}
