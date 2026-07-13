import { afterEach, describe, expect, it, vi } from 'vitest'

import {
  getInstrumentResearchRating,
  getInstrumentResearchRatings,
  normalizeFundNavSeriesResponse,
  type CanonicalQuoteSeriesResolution,
  type CanonicalQuoteSeriesResolutionSummary,
  updateInstrumentResearchRating,
} from './api'
import apiSource from './api.ts?raw'

const emptyBasisStatistics = {
  nav: {
    latest_date: null,
    latest_value: null,
    latest_change: null,
    latest_change_percent: null,
  },
  nav_with_dividend: {
    latest_date: null,
    latest_value: null,
    latest_change: null,
    latest_change_percent: null,
  },
}

const frequencyProfile = {
  requested_frequency: 'auto' as const,
  resolved_frequency: null,
  inferred_frequency: null,
  source_frequency_counts: { daily: 0, weekly: 0, monthly: 0, unknown: 0 },
  raw_observation_count: 0,
  observation_count: 0,
  start_date: null,
  end_date: null,
  annualization_periods_per_year: null,
  largest_gap_days: null,
  gap_count: 0,
  gap_status: 'unresolved' as const,
  status_label: 'Unavailable',
}

function emptyResolution(instrumentId: string): CanonicalQuoteSeriesResolution {
  return {
    resolution_status: 'unavailable',
    resolver_strategy_version: 'canonical-quote-resolver/v1',
    instrument_id: instrumentId,
    role: 'total_return',
    metric_family: null,
    quote_basis: null,
    currency: 'USD',
    range_mode: 'since_inception',
    start_date: null,
    end_date: '2026-01-31',
    quote_selection_policy_version: 'canonical-quote-selection/v1',
    quote_selection_policy_revision: 'test-policy-revision',
    freshness_policy: {
      policy_version: 'canonical-quote-freshness/v1',
      mode: 'calendar_day_carry_forward',
      max_age_days: 45,
    },
    quote_series_id: null,
    start_boundary_observation: null,
    start_anchor: null,
    observations: [],
    points: [],
    observation_count: 0,
    adopted_point_count: 0,
    first_observation_date: null,
    last_observation_date: null,
    coverage_status: 'unavailable',
    freshness_status: 'missing',
    ingestion_status: 'unknown',
    reliability_status: 'unavailable',
    reason_codes: ['missing_quote_series'],
    calculation_dependency: {
      resolver_strategy_version: 'canonical-quote-resolver/v1',
      freshness_policy_version: 'canonical-quote-freshness/v1',
      freshness_mode: 'calendar_day_carry_forward',
      max_age_days: 45,
      range_mode: 'since_inception',
      start_date: null,
      end_date: '2026-01-31',
      quote_selection_policy_version: 'canonical-quote-selection/v1',
      quote_selection_policy_revision: 'test-policy-revision',
      quote_series_id: null,
      revision_ids: [],
      payload_hashes: [],
      excluded_revision_ids: [],
      excluded_payload_hashes: [],
      fingerprint: `test:${instrumentId}`,
    },
    consumer_freshness_profile: {
      profile: 'periodic_fund_nav',
      policy_version: 'watchlist_quote_consumer.v1',
      reason_code: 'canonical_fund_periodic_publication_window',
      canonical_instrument_type: 'fund',
      resolver_policy: {
        policy_version: 'canonical-quote-freshness/v1',
        mode: 'calendar_day_carry_forward',
        max_age_days: 45,
      },
    },
    consumer_dependency: {
      dependency_kind: 'watchlist_quote_consumer_dependency',
      dependency_version: 'v1',
      canonical_dependency_fingerprint: `test:${instrumentId}`,
      consumer_freshness_profile: {
        profile: 'periodic_fund_nav',
        policy_version: 'watchlist_quote_consumer.v1',
        reason_code: 'canonical_fund_periodic_publication_window',
        canonical_instrument_type: 'fund',
        resolver_policy: {
          policy_version: 'canonical-quote-freshness/v1',
          mode: 'calendar_day_carry_forward',
          max_age_days: 45,
        },
      },
      fingerprint: `sha256:test-${instrumentId}`,
    },
  }
}

function emptyResolutionSummary(
  instrumentId: string,
): CanonicalQuoteSeriesResolutionSummary {
  const {
    observations: _observations,
    points: _points,
    calculation_dependency: {
      revision_ids,
      payload_hashes: _payloadHashes,
      excluded_revision_ids,
      excluded_payload_hashes: _excludedPayloadHashes,
      ...boundedDependency
    },
    ...boundedResolution
  } = emptyResolution(instrumentId)
  return {
    ...boundedResolution,
    schema_version: 'watchlist_quote_resolution_summary.v1',
    calculation_dependency: {
      ...boundedDependency,
      revision_count: revision_ids.length,
      excluded_revision_count: excluded_revision_ids.length,
    },
  }
}

const rowLineage = {
  status: 'complete' as const,
  observation_id: 'observation:test',
  revision_id: 'revision:test',
  revision_number: 1,
  payload_hash: 'payload:test',
  source_ref: 'api-test',
  source_published_at: null,
}

const currentResearchRating = {
  instrument_id: 'fund/1',
  rating_revision_id: 'rating-revision-2',
  revision_number: 2,
  previous_rating_revision_id: 'rating-revision-1',
  rating: 4,
  confidence: 'high' as const,
  as_of_date: '2026-07-14',
  rationale: 'Evidence-backed current revision.',
  author: 'Analyst A',
  next_review_date: '2026-10-14',
  created_at: '2026-07-14T08:30:00Z',
  superseded_at: null,
  is_current: true,
}

function jsonResponse(payload: unknown, status = 200) {
  return new Response(JSON.stringify(payload), {
    status,
    headers: { 'Content-Type': 'application/json' },
  })
}

afterEach(() => {
  vi.unstubAllGlobals()
})

describe('research rating API boundary', () => {
  it('loads the current revision and append-only history from distinct endpoints', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse(currentResearchRating))
      .mockResolvedValueOnce(jsonResponse({ items: [currentResearchRating] }))
    vi.stubGlobal('fetch', fetchMock)

    const [current, history] = await Promise.all([
      getInstrumentResearchRating('fund/1'),
      getInstrumentResearchRatings('fund/1'),
    ])

    expect(current).toEqual(currentResearchRating)
    expect(history.items).toEqual([currentResearchRating])
    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/api/instruments/fund%2F1/research-rating',
    )
    expect(fetchMock.mock.calls[1]?.[0]).toBe(
      '/api/instruments/fund%2F1/research-ratings',
    )
  })

  it('sends the current revision as the compare-and-swap token', async () => {
    const fetchMock = vi.fn().mockResolvedValueOnce(jsonResponse(currentResearchRating))
    vi.stubGlobal('fetch', fetchMock)

    await updateInstrumentResearchRating('fund/1', {
      rating: 4,
      confidence: 'high',
      as_of_date: '2026-07-14',
      rationale: 'Evidence-backed current revision.',
      author: 'Analyst A',
      next_review_date: '2026-10-14',
      expected_current_revision_id: 'rating-revision-1',
    })

    expect(fetchMock.mock.calls[0]?.[0]).toBe(
      '/api/instruments/fund%2F1/research-rating',
    )
    expect(fetchMock.mock.calls[0]?.[1]).toEqual(
      expect.objectContaining({
        method: 'PUT',
        body: expect.stringContaining(
          '"expected_current_revision_id":"rating-revision-1"',
        ),
      }),
    )
  })

  it('preserves the 409 response as a typed API conflict', async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ detail: 'rating revision conflict' }, 409))
    vi.stubGlobal('fetch', fetchMock)

    await expect(
      updateInstrumentResearchRating('fund/1', {
        rating: 3,
        confidence: 'medium',
        as_of_date: '2026-07-14',
        rationale: 'Conflicting draft.',
        author: 'Analyst B',
        expected_current_revision_id: 'stale-revision',
      }),
    ).rejects.toMatchObject({
      name: 'ApiRequestError',
      status: 409,
      responseBody: '{"detail":"rating revision conflict"}',
    })
  })
})

describe('fund NAV API boundary', () => {
  it('keeps projection quote lineage bounded while NAV retains the full resolution', () => {
    const summary = emptyResolutionSummary('fund-summary')

    expect(summary.schema_version).toBe(
      'watchlist_quote_resolution_summary.v1',
    )
    expect(summary).not.toHaveProperty('observations')
    expect(summary).not.toHaveProperty('points')
    expect(summary.calculation_dependency).not.toHaveProperty('revision_ids')
    expect(summary.calculation_dependency).not.toHaveProperty('payload_hashes')
    expect(summary.calculation_dependency).not.toHaveProperty(
      'excluded_revision_ids',
    )
    expect(summary.calculation_dependency).not.toHaveProperty(
      'excluded_payload_hashes',
    )
    expect(summary.calculation_dependency.revision_count).toBe(0)
    expect(summary.calculation_dependency.excluded_revision_count).toBe(0)
    expect(summary.calculation_dependency.fingerprint).toBe('test:fund-summary')

    expect(
      apiSource.match(
        /\n  quote_resolution\?: CanonicalQuoteSeriesResolutionSummary/g,
      ),
    ).toHaveLength(3)
    expect(
      apiSource.match(
        /historical_quote_resolution\?: CanonicalQuoteSeriesResolutionSummary \| null/g,
      ),
    ).toHaveLength(2)
    expect(apiSource).toContain('fund: CanonicalQuoteSeriesResolutionSummary')
    expect(apiSource).toContain(
      'benchmark: CanonicalQuoteSeriesResolutionSummary | null',
    )
    expect(apiSource).toContain(
      'resolution?: CanonicalQuoteSeriesResolutionSummary',
    )
    expect(apiSource).toContain(
      'resolution: CanonicalQuoteSeriesResolution | null',
    )
  })

  it('omits unavailable quote observations instead of converting null to zero', () => {
    const normalized = normalizeFundNavSeriesResponse({
      instrument_id: 'fund-null-nav',
      valuation_date: '2026-01-31',
      count: 1,
      nav_basis_type: 'nav_with_dividend',
      nav_basis_source: 'shared',
      nav_basis_status: 'ready',
      resolution: emptyResolution('fund-null-nav'),
      calculation_frequency_profile: frequencyProfile,
      basis_statistics: emptyBasisStatistics,
      series: [{ date: '2026-01-31', nav: null, value: null }],
      calculation_series: [{ date: '2026-01-31', nav: null, value: null }],
      rows: [
        {
          date: '2026-01-31',
          nav: null,
          nav_with_dividend: null,
          selected_value: null,
          currency: 'USD',
          frequency: 'monthly',
          adopted_at: null,
          ...rowLineage,
        },
      ],
    })

    expect(normalized.series).toEqual([])
    expect(normalized.calculation_series).toEqual([])
    expect(normalized.rows[0].selected_value).toBeNull()
  })

  it('preserves explicit zero observations when the source actually supplies zero', () => {
    const normalized = normalizeFundNavSeriesResponse({
      instrument_id: 'fund-zero-nav',
      valuation_date: '2026-01-31',
      count: 1,
      nav_basis_type: 'nav',
      nav_basis_source: 'shared',
      nav_basis_status: 'ready',
      resolution: emptyResolution('fund-zero-nav'),
      calculation_frequency_profile: frequencyProfile,
      basis_statistics: emptyBasisStatistics,
      series: [{ date: '2026-01-31', value: 0 }],
      rows: [
        {
          date: '2026-01-31',
          nav: 0,
          nav_with_dividend: null,
          selected_value: 0,
          currency: 'USD',
          frequency: 'monthly',
          adopted_at: null,
          ...rowLineage,
        },
      ],
    })

    expect(normalized.series).toEqual([
      expect.objectContaining({ date: '2026-01-31', value: 0, nav: 0 }),
    ])
  })

  it('does not reconstruct a missing selected series value from a basis column', () => {
    const normalized = normalizeFundNavSeriesResponse({
      instrument_id: 'fund-missing-selection',
      valuation_date: '2026-01-31',
      count: 1,
      nav_basis_type: 'nav',
      nav_basis_source: 'shared',
      nav_basis_status: 'ready',
      resolution: emptyResolution('fund-missing-selection'),
      calculation_frequency_profile: frequencyProfile,
      basis_statistics: emptyBasisStatistics,
      rows: [
        {
          date: '2026-01-31',
          nav: 101,
          nav_with_dividend: 102,
          selected_basis_type: 'nav',
          selected_value: null,
          currency: 'USD',
          frequency: 'monthly',
          adopted_at: null,
          ...rowLineage,
        },
      ],
    })

    expect(normalized.series).toEqual([])
    expect(normalized.calculation_series).toEqual([])
  })
})
