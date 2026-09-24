import HorizontalTableScroll from '../../../../../packages/ui/src/HorizontalTableScroll'
import { usePortfolioAccess } from '../components/PortfolioAccessProvider'
import { useEffect, useLayoutEffect, useMemo, useRef, useState } from 'react'
import { Link, useParams } from 'react-router'

import BenchmarkSearchBox, { benchmarkInstrumentLabel } from '../components/BenchmarkSearchBox'
import CalculationStatus from '../components/CalculationStatus'
import InfoHint from '../components/InfoHint'
import ResearchComparisonPanel from '../components/ResearchComparisonPanel'
import ResearchSolutionTree from '../components/ResearchSolutionTree'
import ResearchSleeveCharts from '../components/ResearchSleeveCharts'
import PortfolioWorkspaceLayout from '../components/PortfolioWorkspaceLayout'
import QualityWarningsNotice from '../components/QualityWarningsNotice'
import NoticeToast, { type NoticeToastMessage } from '../../../../../packages/ui/src/NoticeToast'
import {
  beginRequest,
  invalidateRequests,
  isRequestCurrent,
} from '../../../../../packages/ui/src/requestIdentity'
import { useModalDialog } from '../../../../../packages/ui/src/useModalDialog'
import {
  createPortfolioResearchRun,
  getPortfolioResearchBacktestBenchmarkComparison,
  getPortfolioInstruments,
  getPortfolioRiskPolicy,
  getPortfolioTaxonomyCatalog,
  getPortfolioResearchRun,
  getPortfolioResearchWorkbench,
  updatePortfolioResearchSettings,
  type PortfolioResearchBacktestBenchmarkComparisonResponse,
  type PortfolioResearchBacktestRebalanceFrequency,
  type PortfolioResearchAsOfMode,
  type PortfolioResearchCapitalMode,
  type PortfolioResearchPlanningScopeOption,
  type PortfolioResearchTopSleeveWeightBoundRecord,
  type PortfolioResearchWorkbenchResponse,
  type PortfolioTaxonomyNodeRecord,
  type PortfolioTaxonomyRecord,
  type SharedInstrumentRecord,
} from '../lib/api'
import {
  formatLabel,
  formatNumber,
  formatPercent,
  formatPercentInput,
} from '../lib/format'
import { resolveResearchAsOfDraft, serializeResearchAsOf } from '../lib/researchAsOf'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import '../components/taxonomy-features.css'

const CAPITAL_MODE_OPTIONS = [
  { value: 'unit_notional', label: 'Unit' },
  { value: 'fixed_gross', label: 'Fixed Gross' },
  { value: 'target_volatility', label: 'Vol Target' },
  { value: 'volatility_cap', label: 'Vol Cap' },
] as const

const REBALANCE_OPTIONS: Array<{ value: PortfolioResearchBacktestRebalanceFrequency; label: string }> = [
  { value: '1w', label: '1W' },
  { value: '1m', label: '1M' },
  { value: '3m', label: '3M' },
]

type ResearchRunSetupDraft = {
  planningTaxonomyId: string
  asOfMode: PortfolioResearchAsOfMode
  asOfDate: string
  notes: string | null
  capitalMode: PortfolioResearchCapitalMode
  grossExposure: string
  targetVolatilityPct: string
  maxGrossExposure: string
  frozenNodeIds: string[]
  topSleeveBounds: ResearchTopSleeveBoundDraft[]
  backtestRebalanceFrequency: PortfolioResearchBacktestRebalanceFrequency
  benchmarkInstrumentId: string
  cashYieldPct: string
  commissionBps: string
  taxBps: string
  slippageBps: string
  implementationDelayDays: string
}

type ResearchTopSleeveBoundDraft = {
  taxonomyNodeId: string
  minWeightPct: string
  maxWeightPct: string
}

function TableStatusRow({
  colSpan,
  label,
  tone = 'neutral',
  detail,
}: {
  colSpan: number
  label: string
  tone?: 'neutral' | 'error'
  detail?: string | null
}) {
  return (
    <tr className="table-status-row">
      <td
        colSpan={colSpan}
        className={`empty-state-cell ${tone === 'error' ? 'table-status-cell-error' : ''}`}
        title={detail ?? undefined}
        aria-label={detail ? `${label}. ${detail}` : undefined}
        tabIndex={detail ? 0 : undefined}
      >
        {label}
      </td>
    </tr>
  )
}

function extractErrorMessage(error: unknown) {
  if (error instanceof Error) {
    return error.message && error.message !== '[object Object]' ? error.message : 'Request failed.'
  }
  if (typeof error === 'object' && error && 'message' in error && typeof error.message === 'string') {
    return error.message && error.message !== '[object Object]' ? error.message : 'Request failed.'
  }
  return 'Request failed.'
}

function formatTimestamp(value: string | null | undefined) {
  if (!value) {
    return '-'
  }
  return value.replace('T', ' ').replace(/:\d{2}(?:\.\d+)?Z$/, ' UTC')
}

function resolveStatusLabel(status: string) {
  if (status === 'completed') {
    return 'Completed'
  }
  if (status === 'failed') {
    return 'Failed'
  }
  if (status === 'running') {
    return 'Running'
  }
  return formatLabel(status)
}

function formatMaybePercent(value: number | null | undefined, digits = 2) {
  return value == null ? '-' : formatPercent(value, digits)
}

function formatMaybeNumber(value: number | null | undefined, digits = 2) {
  return value == null ? '-' : formatNumber(value, digits)
}

function isVolatilityCapitalMode(value: PortfolioResearchCapitalMode) {
  return value === 'target_volatility' || value === 'volatility_cap'
}

function sortTaxonomyNodes(nodes: PortfolioTaxonomyNodeRecord[]) {
  return [...nodes].sort((left, right) => {
    if (left.sort_order !== right.sort_order) {
      return left.sort_order - right.sort_order
    }
    return left.node_name.localeCompare(right.node_name) || left.taxonomy_node_id.localeCompare(right.taxonomy_node_id)
  })
}

function buildPlanningScopeOptions(
  taxonomy: PortfolioTaxonomyRecord,
  nodes: PortfolioTaxonomyNodeRecord[],
): PortfolioResearchPlanningScopeOption[] {
  const activeNodes = sortTaxonomyNodes(
    nodes.filter((node) => node.taxonomy_id === taxonomy.taxonomy_id && node.status === 'active'),
  )
  const childrenByParent = new Map<string | null, PortfolioTaxonomyNodeRecord[]>()
  activeNodes.forEach((node) => {
    const parentKey = node.parent_taxonomy_node_id ?? null
    const currentChildren = childrenByParent.get(parentKey) ?? []
    currentChildren.push(node)
    childrenByParent.set(parentKey, currentChildren)
  })

  const options: PortfolioResearchPlanningScopeOption[] = [
    {
      taxonomy_node_id: null,
      label: 'Top Level',
      path: 'Top Level',
      depth: 0,
      allocation_basis: taxonomy.root_allocation_basis ?? 'weight',
      has_children: Boolean(childrenByParent.get(null)?.length),
    },
  ]
  const visited = new Set<string>()

  function appendNode(node: PortfolioTaxonomyNodeRecord, parentPath: string, depth: number) {
    if (visited.has(node.taxonomy_node_id)) {
      return
    }
    visited.add(node.taxonomy_node_id)
    const path = `${parentPath} / ${node.node_name}`
    options.push({
      taxonomy_node_id: node.taxonomy_node_id,
      label: node.node_name,
      path,
      depth,
      allocation_basis: node.allocation_basis ?? 'weight',
      has_children: Boolean(childrenByParent.get(node.taxonomy_node_id)?.length),
    })
    ;(childrenByParent.get(node.taxonomy_node_id) ?? []).forEach((child) => appendNode(child, path, depth + 1))
  }

  ;(childrenByParent.get(null) ?? []).forEach((node) => appendNode(node, 'Top Level', 1))
  activeNodes.forEach((node) => appendNode(node, 'Top Level', 1))
  return options
}

export default function ResearchPage() {
  const zh = useLanguage().language === 'zh-Hans'
  const canEditPortfolio = Boolean(usePortfolioAccess()?.can_edit)
  const { portfolioId = '' } = useParams()
  const [workbench, setWorkbench] = useState<PortfolioResearchWorkbenchResponse | null>(null)
  const [loading, setLoading] = useState(true)
  const [runDetailLoading, setRunDetailLoading] = useState(false)
  const [runDetailError, setRunDetailError] = useState<string | null>(null)
  const [workspaceError, setWorkspaceError] = useState<string | null>(null)
  const [actionError, setActionError] = useState<string | null>(null)
  const [notice, setNotice] = useState<NoticeToastMessage | null>(null)
  const [actionPending, setActionPending] = useState<'run' | 'save' | null>(null)
  const [frozenMenuOpen, setFrozenMenuOpen] = useState(false)
  const [boundsMenuOpen, setBoundsMenuOpen] = useState(false)
  const [dynamicScopeOptions, setDynamicScopeOptions] = useState<PortfolioResearchPlanningScopeOption[] | null>(null)
  const [scopeOptionsLoading, setScopeOptionsLoading] = useState(false)
  const [scopeOptionsError, setScopeOptionsError] = useState<string | null>(null)
  const [benchmarkInstruments, setBenchmarkInstruments] = useState<SharedInstrumentRecord[]>([])
  const [benchmarkSearch, setBenchmarkSearch] = useState('')
  const [benchmarkComparison, setBenchmarkComparison] =
    useState<PortfolioResearchBacktestBenchmarkComparisonResponse | null>(null)
  const [benchmarkComparisonLoading, setBenchmarkComparisonLoading] = useState(false)
  const [benchmarkComparisonError, setBenchmarkComparisonError] = useState<string | null>(null)
  const frozenMenuRef = useRef<HTMLDivElement | null>(null)
  const boundsMenuRef = useRef<HTMLDivElement | null>(null)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const settingsOpenRef = useRef(false)
  const settingsRequestSequenceRef = useRef(0)
  const settingsDialogRef = useModalDialog(settingsOpen, closeSettings)
  const workbenchRequestSequenceRef = useRef(0)
  const runRequestSequenceRef = useRef(0)
  const currentPortfolioIdRef = useRef(portfolioId)
  const runSetupDraftRef = useRef<ResearchRunSetupDraft>({
    planningTaxonomyId: '',
    asOfMode: 'dynamic',
    asOfDate: '',
    notes: null,
    capitalMode: 'unit_notional',
    grossExposure: '',
    targetVolatilityPct: '',
    maxGrossExposure: '',
    frozenNodeIds: [],
    topSleeveBounds: [],
    backtestRebalanceFrequency: '1m',
    benchmarkInstrumentId: '',
    cashYieldPct: '2',
    commissionBps: '2',
    taxBps: '10',
    slippageBps: '5',
    implementationDelayDays: '1',
  })

  const savedRunSetupDraftRef = useRef<ResearchRunSetupDraft>(runSetupDraftRef.current)

  const [planningTaxonomyId, setPlanningTaxonomyId] = useState('')
  const availableTaxonomyIdsRef = useRef(new Set<string>())
  const [asOfMode, setAsOfMode] = useState<PortfolioResearchAsOfMode>('dynamic')
  const [asOfDate, setAsOfDate] = useState('')
  const [capitalMode, setCapitalMode] = useState<PortfolioResearchCapitalMode>('unit_notional')
  const [grossExposure, setGrossExposure] = useState('')
  const [targetVolatilityPct, setTargetVolatilityPct] = useState('')
  const [maxGrossExposure, setMaxGrossExposure] = useState('')
  const [frozenNodeIds, setFrozenNodeIds] = useState<string[]>([])
  const [topSleeveBounds, setTopSleeveBounds] = useState<ResearchTopSleeveBoundDraft[]>([])
  const [backtestRebalanceFrequency, setBacktestRebalanceFrequency] = useState<PortfolioResearchBacktestRebalanceFrequency>('1m')
  const [benchmarkInstrumentId, setBenchmarkInstrumentId] = useState('')
  const [cashYieldPct, setCashYieldPct] = useState('2')
  const [commissionBps, setCommissionBps] = useState('2')
  const [taxBps, setTaxBps] = useState('10')
  const [slippageBps, setSlippageBps] = useState('5')
  const [implementationDelayDays, setImplementationDelayDays] = useState('1')

  settingsOpenRef.current = settingsOpen
  currentPortfolioIdRef.current = portfolioId
  availableTaxonomyIdsRef.current = new Set(
    workbench?.portfolio_id === portfolioId ? workbench.planning_taxonomy_options.map((item) => item.taxonomy_id) : [],
  )

  function applyRunSetupDraft(nextDraft: ResearchRunSetupDraft) {
    setPlanningTaxonomyId(nextDraft.planningTaxonomyId)
    setAsOfMode(nextDraft.asOfMode)
    setAsOfDate(nextDraft.asOfDate)
    setCapitalMode(nextDraft.capitalMode)
    setGrossExposure(nextDraft.grossExposure)
    setTargetVolatilityPct(nextDraft.targetVolatilityPct)
    setMaxGrossExposure(nextDraft.maxGrossExposure)
    setFrozenNodeIds(nextDraft.frozenNodeIds)
    setTopSleeveBounds(nextDraft.topSleeveBounds)
    setBacktestRebalanceFrequency(nextDraft.backtestRebalanceFrequency)
    setBenchmarkInstrumentId(nextDraft.benchmarkInstrumentId)
    const benchmark = benchmarkInstruments.find((instrument) => instrument.instrument_id === nextDraft.benchmarkInstrumentId)
    setBenchmarkSearch(benchmark ? benchmarkInstrumentLabel(benchmark) : '')
    setCashYieldPct(nextDraft.cashYieldPct)
    setCommissionBps(nextDraft.commissionBps)
    setTaxBps(nextDraft.taxBps)
    setSlippageBps(nextDraft.slippageBps)
    setImplementationDelayDays(nextDraft.implementationDelayDays)
    runSetupDraftRef.current = nextDraft
  }

  function closeSettings() {
    if (actionPending === 'save') return
    applyRunSetupDraft(savedRunSetupDraftRef.current)
    setSettingsOpen(false)
    setFrozenMenuOpen(false)
    setBoundsMenuOpen(false)
    setActionError(null)
  }

  function openSettings() {
    applyRunSetupDraft(savedRunSetupDraftRef.current)
    setActionError(null)
    setSettingsOpen(true)
  }

  async function reloadWorkbench(targetPortfolioId = portfolioId) {
    if (targetPortfolioId && currentPortfolioIdRef.current !== targetPortfolioId) {
      return
    }
    const request = beginRequest(workbenchRequestSequenceRef, targetPortfolioId)
    if (!targetPortfolioId) {
      setWorkbench(null)
      setLoading(false)
      setWorkspaceError(null)
      return
    }
    setLoading(true)
    try {
      const response = await getPortfolioResearchWorkbench(targetPortfolioId)
      if (
        !isRequestCurrent(
          workbenchRequestSequenceRef,
          request,
          currentPortfolioIdRef.current,
          response.portfolio_id,
        )
      ) {
        return
      }
      setWorkbench(response)
      setWorkspaceError(null)
    } catch (error) {
      if (!isRequestCurrent(workbenchRequestSequenceRef, request, currentPortfolioIdRef.current)) {
        return
      }
      setWorkbench(null)
      setWorkspaceError(extractErrorMessage(error))
    } finally {
      if (isRequestCurrent(workbenchRequestSequenceRef, request, currentPortfolioIdRef.current)) {
        setLoading(false)
      }
    }
  }

  useEffect(() => {
    invalidateRequests(workbenchRequestSequenceRef)
    invalidateRequests(runRequestSequenceRef)
    invalidateRequests(settingsRequestSequenceRef)
    setSettingsOpen(false)
    settingsOpenRef.current = false
    setWorkbench(null)
    setRunDetailLoading(false)
    setRunDetailError(null)
    setWorkspaceError(null)
    setActionPending(null)
    setActionError(null)
    setNotice(null)
    setFrozenMenuOpen(false)
    setBoundsMenuOpen(false)
    void reloadWorkbench(portfolioId)
    return () => {
      invalidateRequests(workbenchRequestSequenceRef)
      invalidateRequests(runRequestSequenceRef)
      invalidateRequests(settingsRequestSequenceRef)
    }
  }, [portfolioId])

  useEffect(() => {
    if (!portfolioId) {
      setBenchmarkInstruments([])
      return
    }
    let cancelled = false
    getPortfolioInstruments(portfolioId)
      .then((response) => {
        if (!cancelled) {
          setBenchmarkInstruments(response.instruments)
        }
      })
      .catch(() => {
        if (!cancelled) {
          setBenchmarkInstruments([])
        }
      })
    return () => {
      cancelled = true
    }
  }, [portfolioId])

  useEffect(() => {
    function handleRiskPolicyUpdated(event: Event) {
      const detail = (event as CustomEvent<{ portfolioId?: string }>).detail
      if (detail?.portfolioId === portfolioId) {
        void reloadWorkbench()
      }
    }
    window.addEventListener('portfolio-risk-policy-updated', handleRiskPolicyUpdated)
    return () => window.removeEventListener('portfolio-risk-policy-updated', handleRiskPolicyUpdated)
  }, [portfolioId])

    useEffect(() => {
      function handleClick(event: MouseEvent) {
        const target = event.target as Node | null
        if (frozenMenuOpen && frozenMenuRef.current && target && !frozenMenuRef.current.contains(target)) {
          setFrozenMenuOpen(false)
        }
        if (boundsMenuOpen && boundsMenuRef.current && target && !boundsMenuRef.current.contains(target)) {
          setBoundsMenuOpen(false)
        }
      }
      document.addEventListener('mousedown', handleClick)
      return () => document.removeEventListener('mousedown', handleClick)
    }, [boundsMenuOpen, frozenMenuOpen])

  useLayoutEffect(() => {
    if (!workbench || workbench.portfolio_id !== portfolioId) {
      return
    }
    const requestedTaxonomyId = workbench.settings.planning_taxonomy_id ?? ''
    const nextPlanningTaxonomyId = requestedTaxonomyId
      ? (availableTaxonomyIdsRef.current.has(requestedTaxonomyId) ? requestedTaxonomyId : '')
      : workbench.planning_taxonomy_options.length === 1 ? workbench.planning_taxonomy_options[0].taxonomy_id : ''
    const nextAsOf = resolveResearchAsOfDraft(workbench.settings, workbench.as_of_date)
    const nextBenchmarkInstrumentId = workbench.settings.backtest_benchmark_instrument_id ?? ''
    const nextCapitalMode = workbench.settings.capital_mode
    const nextTopSleeveBounds = (workbench.settings.top_sleeve_weight_bounds ?? []).map((item) => ({
      taxonomyNodeId: item.taxonomy_node_id,
      minWeightPct: formatPercentInput(item.min_weight),
      maxWeightPct: formatPercentInput(item.max_weight),
    }))
    const nextDraft: ResearchRunSetupDraft = {
      planningTaxonomyId: nextPlanningTaxonomyId,
      ...nextAsOf,
      notes: workbench.settings.notes ?? null,
      capitalMode: nextCapitalMode,
      grossExposure: workbench.settings.gross_exposure != null ? String(workbench.settings.gross_exposure) : '',
      targetVolatilityPct:
        formatPercentInput(workbench.settings.target_volatility),
      maxGrossExposure:
        workbench.settings.max_gross_exposure != null
          ? String(workbench.settings.max_gross_exposure)
          : nextCapitalMode === 'target_volatility'
            ? '1'
            : '',
      frozenNodeIds: workbench.settings.frozen_taxonomy_node_ids ?? [],
      topSleeveBounds: nextTopSleeveBounds,
      backtestRebalanceFrequency: workbench.settings.backtest_rebalance_frequency ?? '1m',
      benchmarkInstrumentId: nextBenchmarkInstrumentId,
      cashYieldPct: String(workbench.settings.backtest_cash_yield_annual * 100),
      commissionBps: String(workbench.settings.backtest_commission_bps),
      taxBps: String(workbench.settings.backtest_tax_bps),
      slippageBps: String(workbench.settings.backtest_slippage_bps),
      implementationDelayDays: String(workbench.settings.backtest_implementation_delay_days),
    }
    savedRunSetupDraftRef.current = nextDraft
    if (!settingsOpenRef.current) {
      applyRunSetupDraft(nextDraft)
    } else if (runSetupDraftRef.current.planningTaxonomyId && !availableTaxonomyIdsRef.current.has(runSetupDraftRef.current.planningTaxonomyId)) {
      applyRunSetupDraft({ ...runSetupDraftRef.current, planningTaxonomyId: '', frozenNodeIds: [], topSleeveBounds: [] })
    }
  }, [workbench])

  useEffect(() => {
    const selectedBenchmark = benchmarkInstruments.find((instrument) => instrument.instrument_id === benchmarkInstrumentId)
    if (selectedBenchmark) {
      setBenchmarkSearch(benchmarkInstrumentLabel(selectedBenchmark))
    } else if (!benchmarkInstrumentId) {
      setBenchmarkSearch('')
    }
  }, [benchmarkInstrumentId, benchmarkInstruments])

  useEffect(() => {
    const savedPlanningTaxonomyId = workbench?.settings.planning_taxonomy_id ?? ''
    if (!portfolioId || !workbench || !planningTaxonomyId || planningTaxonomyId === savedPlanningTaxonomyId) {
      setDynamicScopeOptions(null)
      setScopeOptionsLoading(false)
      setScopeOptionsError(null)
      return
    }
    let cancelled = false
    setScopeOptionsLoading(true)
    setScopeOptionsError(null)
    getPortfolioTaxonomyCatalog(portfolioId)
      .then((catalog) => {
        if (cancelled) {
          return
        }
        const taxonomy = catalog.taxonomies.find(
          (item) => item.taxonomy_id === planningTaxonomyId && item.status === 'active',
        )
        if (!taxonomy) {
          throw new Error('Selected planning taxonomy is unavailable.')
        }
        setDynamicScopeOptions(buildPlanningScopeOptions(taxonomy, catalog.taxonomy_nodes))
      })
      .catch((error) => {
        if (!cancelled) {
          setDynamicScopeOptions(null)
          setScopeOptionsError(extractErrorMessage(error))
        }
      })
      .finally(() => {
        if (!cancelled) {
          setScopeOptionsLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [planningTaxonomyId, portfolioId, workbench])

  const latestRun = workbench?.selected_run ?? workbench?.runs[0] ?? null

  useEffect(() => {
    const researchRunId = latestRun?.research_run_id ?? ''
    if (
      !portfolioId
      || !researchRunId
      || latestRun?.status !== 'completed'
      || latestRun.detail != null
    ) {
      setRunDetailLoading(false)
      setRunDetailError(null)
      return undefined
    }

    let cancelled = false
    setRunDetailLoading(true)
    setRunDetailError(null)
    getPortfolioResearchRun(portfolioId, researchRunId)
      .then((detailedRun) => {
        if (cancelled || currentPortfolioIdRef.current !== portfolioId) {
          return
        }
        setWorkbench((current) => {
          if (!current || current.portfolio_id !== portfolioId) {
            return current
          }
          const compactRun = (
            current.selected_run?.research_run_id === researchRunId
              ? current.selected_run
              : current.runs.find((run) => run.research_run_id === researchRunId)
          )
          if (!compactRun) {
            return current
          }
          return {
            ...current,
            detail_level: 'selected_run',
            selected_run: {
              ...detailedRun,
              reliability_state: compactRun.reliability_state,
              is_current: compactRun.is_current,
              reliability_reasons: compactRun.reliability_reasons,
            },
          }
        })
      })
      .catch((error) => {
        if (!cancelled) {
          setRunDetailError(extractErrorMessage(error))
        }
      })
      .finally(() => {
        if (!cancelled) {
          setRunDetailLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [latestRun?.detail, latestRun?.research_run_id, latestRun?.status, portfolioId])

  useEffect(() => {
    const researchRunId = latestRun?.research_run_id ?? ''
    const selectedBenchmarkId = workbench?.settings.backtest_benchmark_instrument_id?.trim() ?? ''
    const storedBenchmarkId = latestRun?.detail?.backtest_benchmark?.instrument_id ?? ''
    if (
      !portfolioId
      || !researchRunId
      || latestRun?.status !== 'completed'
      || latestRun.detail == null
      || !selectedBenchmarkId
    ) {
      setBenchmarkComparison(null)
      setBenchmarkComparisonLoading(false)
      setBenchmarkComparisonError(null)
      return undefined
    }
    if (selectedBenchmarkId === storedBenchmarkId && latestRun.detail?.backtest_benchmark) {
      setBenchmarkComparison(null)
      setBenchmarkComparisonLoading(false)
      setBenchmarkComparisonError(null)
      return undefined
    }

    let cancelled = false
    setBenchmarkComparison(null)
    setBenchmarkComparisonLoading(true)
    setBenchmarkComparisonError(null)
    getPortfolioResearchBacktestBenchmarkComparison(portfolioId, researchRunId, selectedBenchmarkId)
      .then((response) => {
        if (!cancelled) {
          setBenchmarkComparison(response)
        }
      })
      .catch((error) => {
        if (!cancelled) {
          setBenchmarkComparison(null)
          setBenchmarkComparisonError(extractErrorMessage(error))
        }
      })
      .finally(() => {
        if (!cancelled) {
          setBenchmarkComparisonLoading(false)
        }
      })
    return () => {
      cancelled = true
    }
  }, [
    workbench?.settings.backtest_benchmark_instrument_id,
    latestRun?.detail?.backtest_benchmark,
    latestRun?.research_run_id,
    latestRun?.status,
    portfolioId,
  ])

  const selectedScopeOptions = useMemo<PortfolioResearchPlanningScopeOption[]>(() => {
    if (!workbench || !planningTaxonomyId) {
      return []
    }
    if (planningTaxonomyId === (workbench.settings.planning_taxonomy_id ?? '')) {
      return workbench.planning_scope_options
    }
    return dynamicScopeOptions ?? []
  }, [dynamicScopeOptions, planningTaxonomyId, workbench])
  const savedPlanningTaxonomyId = workbench?.settings.planning_taxonomy_id ?? ''
  const effectiveSavedTaxonomyId = savedPlanningTaxonomyId
    ? (availableTaxonomyIdsRef.current.has(savedPlanningTaxonomyId) ? savedPlanningTaxonomyId : '')
    : workbench?.planning_taxonomy_options.length === 1 ? workbench.planning_taxonomy_options[0].taxonomy_id : ''
  const scopeOptionsPending = Boolean(
    planningTaxonomyId && planningTaxonomyId !== savedPlanningTaxonomyId && !dynamicScopeOptions && !scopeOptionsError,
  )
  const scopeActionBlocked = scopeOptionsLoading || scopeOptionsPending || Boolean(scopeOptionsError)
    const selectableFrozenScopes = useMemo(
      () => selectedScopeOptions.filter((option) => Boolean(option.taxonomy_node_id)),
      [selectedScopeOptions],
    )
    const topSleeveOptions = useMemo(
      () => selectedScopeOptions.filter((option) => option.depth === 1 && Boolean(option.taxonomy_node_id)),
      [selectedScopeOptions],
    )
    const selectedFrozenLabels = useMemo(() => {
      const labelByNodeId = new Map(selectableFrozenScopes.map((option) => [option.taxonomy_node_id ?? '', option.label]))
      return frozenNodeIds.map((nodeId) => labelByNodeId.get(nodeId)).filter(Boolean) as string[]
    }, [frozenNodeIds, selectableFrozenScopes])
    const frozenMenuLabel = selectedFrozenLabels.length ? `${selectedFrozenLabels.length} selected` : 'None'
    const configuredBoundCount = topSleeveBounds.filter(
      (item) => item.minWeightPct.trim() || item.maxWeightPct.trim(),
    ).length
    const boundsMenuLabel = configuredBoundCount ? `${configuredBoundCount} set` : 'None'
    const topSleeveBoundByNodeId = useMemo(
      () => new Map(topSleeveBounds.map((item) => [item.taxonomyNodeId, item])),
      [topSleeveBounds],
    )

  function updateRunSetupDraft(updates: Partial<ResearchRunSetupDraft>) {
    const nextDraft = {
      ...runSetupDraftRef.current,
      ...updates,
    }
    setActionError(null)
    runSetupDraftRef.current = nextDraft
    return nextDraft
  }

    function toggleFrozenNode(nodeId: string) {
      setFrozenNodeIds((current) => {
      if (current.includes(nodeId)) {
        const nextFrozenNodeIds = current.filter((item) => item !== nodeId)
        updateRunSetupDraft({ frozenNodeIds: nextFrozenNodeIds })
        return nextFrozenNodeIds
      }
      const nextFrozenNodeIds = [...current, nodeId]
      updateRunSetupDraft({ frozenNodeIds: nextFrozenNodeIds })
      return nextFrozenNodeIds
      })
    }

    function updateTopSleeveBound(nodeId: string, field: 'minWeightPct' | 'maxWeightPct', value: string) {
      setTopSleeveBounds((current) => {
        const existing = current.find((item) => item.taxonomyNodeId === nodeId)
        const nextItem: ResearchTopSleeveBoundDraft = {
          taxonomyNodeId: nodeId,
          minWeightPct: existing?.minWeightPct ?? '',
          maxWeightPct: existing?.maxWeightPct ?? '',
          [field]: value,
        }
        const nextBounds = [
          ...current.filter((item) => item.taxonomyNodeId !== nodeId),
          nextItem,
        ].filter((item) => item.minWeightPct.trim() || item.maxWeightPct.trim())
        updateRunSetupDraft({ topSleeveBounds: nextBounds })
        return nextBounds
      })
    }

    function serializeTopSleeveBounds(
      draftBounds: ResearchTopSleeveBoundDraft[],
    ): PortfolioResearchTopSleeveWeightBoundRecord[] {
      const validTopSleeveIds = new Set(topSleeveOptions.map((item) => item.taxonomy_node_id ?? ''))
      return draftBounds
        .filter((item) => validTopSleeveIds.has(item.taxonomyNodeId))
        .map((item) => {
          const minWeight = item.minWeightPct.trim() ? Number(item.minWeightPct) / 100 : null
          const maxWeight = item.maxWeightPct.trim() ? Number(item.maxWeightPct) / 100 : null
          return {
            taxonomy_node_id: item.taxonomyNodeId,
            min_weight: minWeight,
            max_weight: maxWeight,
          }
        })
        .filter((item) => item.min_weight != null || item.max_weight != null)
    }

  async function persistSettings({
    draft = runSetupDraftRef.current,
    targetPortfolioId = portfolioId,
  }: {
    draft?: ResearchRunSetupDraft
    targetPortfolioId?: string
  } = {}) {
    if (!targetPortfolioId || currentPortfolioIdRef.current !== targetPortfolioId) {
      return null
    }
    const parsedGrossExposure = draft.grossExposure.trim() ? Number(draft.grossExposure) : null
    const parsedTargetVolatility = draft.targetVolatilityPct.trim() ? Number(draft.targetVolatilityPct) / 100 : null
    const parsedMaxGrossExposure = draft.maxGrossExposure.trim() ? Number(draft.maxGrossExposure) : null
    const parsedCashYield = Number(draft.cashYieldPct) / 100
    const parsedCommissionBps = Number(draft.commissionBps)
    const parsedTaxBps = Number(draft.taxBps)
    const parsedSlippageBps = Number(draft.slippageBps)
    const parsedImplementationDelayDays = Number(draft.implementationDelayDays)
    const volatilityMode = isVolatilityCapitalMode(draft.capitalMode)
    const riskPolicy = await getPortfolioRiskPolicy(targetPortfolioId)
    if (currentPortfolioIdRef.current !== targetPortfolioId) {
      return null
    }
    if (draft.planningTaxonomyId && !availableTaxonomyIdsRef.current.has(draft.planningTaxonomyId)) {
      throw new Error('Selected optimization taxonomy is unavailable. Choose a current taxonomy.')
    }
    const validFrozenNodeIds = new Set(selectableFrozenScopes.map((item) => item.taxonomy_node_id ?? ''))
    const frozenTaxonomyNodeIds = draft.frozenNodeIds.filter((nodeId) => validFrozenNodeIds.has(nodeId))
    const topSleeveWeightBounds = serializeTopSleeveBounds(draft.topSleeveBounds)
    const researchAsOf = serializeResearchAsOf(draft)
    if (researchAsOf.as_of_mode === 'pinned' && !researchAsOf.as_of_date) {
      throw new Error('Select a pinned analysis date.')
    }
    return updatePortfolioResearchSettings(targetPortfolioId, {
      planning_taxonomy_id: draft.planningTaxonomyId || null,
      comparator_taxonomy_node_id: null,
      ...researchAsOf,
      lookback_days: riskPolicy.lookback_days,
      calculation_frequency: riskPolicy.calculation_frequency,
      missing_return_policy: riskPolicy.missing_return_policy,
      covariance_model_id: riskPolicy.covariance_model_id,
      contribution_mode: riskPolicy.contribution_mode,
      capital_mode: draft.capitalMode,
      gross_exposure: draft.capitalMode === 'fixed_gross' ? parsedGrossExposure : null,
      target_volatility: volatilityMode ? parsedTargetVolatility : null,
      max_gross_exposure:
        draft.capitalMode === 'target_volatility'
          ? parsedMaxGrossExposure ?? 1
          : null,
      frozen_taxonomy_node_ids: frozenTaxonomyNodeIds,
      top_sleeve_weight_bounds: topSleeveWeightBounds,
      backtest_rebalance_frequency: draft.backtestRebalanceFrequency,
      backtest_benchmark_instrument_id: draft.benchmarkInstrumentId || null,
      backtest_cash_yield_annual: parsedCashYield,
      backtest_commission_bps: parsedCommissionBps,
      backtest_tax_bps: parsedTaxBps,
      backtest_slippage_bps: parsedSlippageBps,
      backtest_implementation_delay_days: parsedImplementationDelayDays,
      notes: draft.notes,
    })
  }

  async function handleSaveSettings() {
    const targetPortfolioId = portfolioId
    if (!canEditPortfolio || actionPending || scopeActionBlocked || !targetPortfolioId) return
    const request = beginRequest(settingsRequestSequenceRef, targetPortfolioId)
    setActionPending('save')
    setActionError(null)
    try {
      const saved = await persistSettings({ targetPortfolioId })
      if (!saved || !isRequestCurrent(settingsRequestSequenceRef, request, currentPortfolioIdRef.current)) return
      setWorkbench((current) => current?.portfolio_id === targetPortfolioId ? { ...current, settings: saved } : current)
      await reloadWorkbench(targetPortfolioId)
      if (!isRequestCurrent(settingsRequestSequenceRef, request, currentPortfolioIdRef.current)) return
      settingsOpenRef.current = false
      setSettingsOpen(false)
      setFrozenMenuOpen(false)
      setBoundsMenuOpen(false)
    } catch (error) {
      if (isRequestCurrent(settingsRequestSequenceRef, request, currentPortfolioIdRef.current)) setActionError(extractErrorMessage(error))
    } finally {
      if (isRequestCurrent(settingsRequestSequenceRef, request, currentPortfolioIdRef.current)) setActionPending(null)
    }
  }

  async function handleRunResearch() {
    const targetPortfolioId = portfolioId
    if (!targetPortfolioId || !canEditPortfolio || actionPending || settingsOpen || !availableTaxonomyIdsRef.current.has(savedRunSetupDraftRef.current.planningTaxonomyId)) {
      return
    }
    const runRequest = beginRequest(runRequestSequenceRef, targetPortfolioId)
    const runDraft = savedRunSetupDraftRef.current
    setActionPending('run')
    setActionError(null)
    setNotice(null)
    setFrozenMenuOpen(false)
    setBoundsMenuOpen(false)
    try {
      await persistSettings({ draft: runDraft, targetPortfolioId })
      if (!isRequestCurrent(runRequestSequenceRef, runRequest, currentPortfolioIdRef.current)) return
      await createPortfolioResearchRun(targetPortfolioId, { requested_by: 'workspace-ui' })
      if (!isRequestCurrent(runRequestSequenceRef, runRequest, currentPortfolioIdRef.current)) {
        return
      }
      setNotice({ id: Date.now(), message: 'Optimization run completed.', tone: 'success' })
      await reloadWorkbench(targetPortfolioId)
    } catch (error) {
      if (!isRequestCurrent(runRequestSequenceRef, runRequest, currentPortfolioIdRef.current)) {
        return
      }
      setActionError(extractErrorMessage(error))
      try {
        await reloadWorkbench(targetPortfolioId)
      } catch {
        // Keep the original run error visible if the follow-up refresh also fails.
      }
    } finally {
      if (isRequestCurrent(runRequestSequenceRef, runRequest, currentPortfolioIdRef.current)) {
        setActionPending(null)
      }
    }
  }

  const backtest = latestRun?.detail?.backtest ?? null
  const usesCurrentTargets = backtest?.methodology?.target_configuration === 'current_snapshot'
  const pointInTimeCoverage = backtest?.point_in_time_coverage ?? null
  const skippedRebalances = pointInTimeCoverage?.skipped_rebalances ?? []
  const pendingRebalances = pointInTimeCoverage?.pending_rebalances ?? []
  const configurationVersionsUsed = pointInTimeCoverage?.configuration_versions_used ?? []
  const executionRecords = backtest?.execution_records ?? []
  const contributionReconciliation = backtest?.contribution_reconciliation_points ?? []
  const latestContributionReconciliation = contributionReconciliation.length
    ? contributionReconciliation[contributionReconciliation.length - 1]
    : null
  const changedTaxonomy = Boolean(latestRun && latestRun.planning_taxonomy_id !== effectiveSavedTaxonomyId)
  const staleRun = latestRun?.status === 'completed' && (latestRun.reliability_state === 'stale' || changedTaxonomy)
  const globalSolverRun = ['global_leaf_covariance_v1', 'global_leaf_scalar_targets_v2', 'global_leaf_scalar_targets_v3', 'global_leaf_scalar_targets_v4', 'global_leaf_scalar_targets_v5'].includes(latestRun?.detail?.solver_version ?? '')
  const solveEvents = (latestRun?.detail?.scope_solve_events ?? []).length
    ? latestRun?.detail?.scope_solve_events ?? []
    : latestRun?.detail?.solve_event
      ? [latestRun.detail.solve_event]
      : []
  const nonExecutionReadySolveEvents = solveEvents.filter((event) => event.execution_ready === false)
  const planningTaxonomyName = workbench?.planning_taxonomy_options.find(
    (option) => option.taxonomy_id === effectiveSavedTaxonomyId,
  )?.name ?? (zh ? '未选择优化分类' : 'No optimization taxonomy selected')
  const savedCapitalMode = workbench?.settings.capital_mode ?? 'unit_notional'
  const savedVolatility = workbench?.settings.target_volatility
  const capitalModeLabel = CAPITAL_MODE_OPTIONS.find((option) => option.value === savedCapitalMode)?.label ?? formatLabel(savedCapitalMode)
  const capitalSummary = isVolatilityCapitalMode(savedCapitalMode)
    ? `${zh ? (savedCapitalMode === 'volatility_cap' ? '波动率上限' : '目标波动率') : (savedCapitalMode === 'volatility_cap' ? 'Volatility cap' : 'Target volatility')} ${formatMaybePercent(savedVolatility)}`
    : savedCapitalMode === 'fixed_gross'
      ? `${zh ? '固定总仓位' : 'Fixed gross'} ${formatMaybePercent(workbench?.settings.gross_exposure)}`
      : (zh ? '单位仓位' : capitalModeLabel)
  const rebalanceFrequency = workbench?.settings.backtest_rebalance_frequency ?? '1m'
  const rebalanceLabel = zh ? ({ '1w': '每周调仓', '1m': '每月调仓', '3m': '每季度调仓' }[rebalanceFrequency]) : `${rebalanceFrequency.toUpperCase()} rebalance`
  const staleRunDetail = [
    'Run Optimization again before using these weights for allocation or orders.',
    ...(latestRun?.reliability_reasons ?? []),
  ].join(' ')
  const constrainedSolveDetail = nonExecutionReadySolveEvents
    .map(
      (event) =>
        `${event.scope_label}: ${
          event.solver_message ?? 'Review target shares and weight bounds before using these weights.'
        }`,
    )
    .join(' ')
  const skippedRebalanceDetail = skippedRebalances
    .map((item) => `${item.date}: ${item.reason}`)
    .join(' ')
  const storedBenchmark = latestRun?.detail?.backtest_benchmark ?? null
  const selectedBenchmarkId = workbench?.settings.backtest_benchmark_instrument_id?.trim() ?? ''
  const displayBenchmark = selectedBenchmarkId
    ? benchmarkComparison?.backtest_benchmark ??
      (selectedBenchmarkId === (storedBenchmark?.instrument_id ?? '') ? storedBenchmark : null)
    : null

  return (
    <PortfolioWorkspaceLayout
      activeSection="Portfolio Optimization"
      busy={loading || runDetailLoading}
    >
      <NoticeToast notice={notice} onDismiss={() => setNotice(null)} />
      {workspaceError ? <div className="inline-notice inline-notice-error">{workspaceError}</div> : null}
      {actionError && !settingsOpen ? <div className="inline-notice inline-notice-error" role="alert">{actionError}</div> : null}
      {loading && !workbench ? <CalculationStatus /> : null}

      {!loading && !workbench && !workspaceError ? <div className="empty-state">No data.</div> : null}

      {workbench ? (
        <>
          <section className="panel research-command-panel">
            <div className="research-command-bar">
              <div className="research-command-copy">
                <div className="panel-title">Optimization Configuration</div>
                <div className="research-command-meta">
                  <QualityWarningsNotice warnings={workbench.current_context.quality_warnings} />
                  <span translate="no">{planningTaxonomyName}</span>
                  <span>{zh ? '数据截至' : 'Data cutoff'} · {workbench.settings.as_of_mode === 'dynamic' ? workbench.as_of_date : workbench.settings.as_of_date || '-'}</span>
                  <span>{zh ? '当前目标' : 'Current targets'}</span>
                  <span>{capitalSummary}</span>
                  <span>{rebalanceLabel}</span>
                </div>
              </div>
              <div className="research-command-actions">
              <button type="button" className="toolbar-link" onClick={openSettings} disabled={Boolean(actionPending)}>{zh ? '优化参数' : 'Optimization Parameters'}</button>
              <button
                type="button"
                className="toolbar-link button-primary research-run-button"
                onClick={() => void handleRunResearch()}
                disabled={!canEditPortfolio || !effectiveSavedTaxonomyId || Boolean(actionPending) || settingsOpen}
              >
                {actionPending === 'run' ? 'Running...' : 'Run Optimization'}
              </button>
              </div>
            </div>
          </section>
          {settingsOpen ? <div className="taxonomy-modal-overlay" role="presentation" onClick={closeSettings}>
            <div ref={settingsDialogRef} className="taxonomy-modal research-parameters-modal" role="dialog" aria-modal="true" aria-label={zh ? '优化参数' : 'Optimization Parameters'} tabIndex={-1} onClick={(event) => event.stopPropagation()}>
              <div className="taxonomy-modal-header"><div className="panel-title">{zh ? '优化参数' : 'Optimization Parameters'}</div>
                <button type="button" className="table-inline-button" onClick={closeSettings} disabled={actionPending === 'save'}>{zh ? '关闭' : 'Close'}</button>
              </div>
              <form
                className="transaction-form taxonomy-form-compact research-run-form"
                aria-busy={actionPending === 'save'}
                onSubmit={(event) => { event.preventDefault(); void handleSaveSettings() }}
              >
                <div className="research-parameters-body">
                {actionError ? <div className="inline-notice inline-notice-error" role="alert">{actionError}</div> : null}
                <fieldset className="research-settings-fieldset" disabled={!canEditPortfolio || actionPending === 'save'}>
                <div className="taxonomy-form-grid taxonomy-form-grid-wide research-settings-grid">
                  <div className="research-parameter-group-title">{zh ? '范围与数据' : 'Scope & Data'}</div>
                <div className="research-taxonomy-choice">
                  <div className="research-taxonomy-field">
                    <span><label htmlFor="research-taxonomy">{zh ? '优化分类' : 'Optimization taxonomy'}</label><InfoHint label={zh ? '优化分类' : 'Optimization taxonomy'} detail={zh ? '优化与回测统一使用本次运行保存的当前目标。日期设置只限定行情和持仓数据。' : 'Optimization and backtests use the current targets saved with each run. Date settings only limit market and holdings data.'} /></span>
                    <select id="research-taxonomy" aria-label={zh ? '优化分类' : 'Optimization taxonomy'} value={planningTaxonomyId} onChange={(event) => {
                      setPlanningTaxonomyId(event.target.value)
                      setFrozenNodeIds([])
                      setTopSleeveBounds([])
                      updateRunSetupDraft({ planningTaxonomyId: event.target.value, frozenNodeIds: [], topSleeveBounds: [] })
                    }}>
                      {!workbench.planning_taxonomy_options.some((item) => item.taxonomy_id === planningTaxonomyId) && <option value="">{zh ? '请选择优化分类' : 'Select an optimization taxonomy'}</option>}
                      {workbench.planning_taxonomy_options.map((taxonomy) => <option key={taxonomy.taxonomy_id} value={taxonomy.taxonomy_id} translate="no">{taxonomy.name}{taxonomy.targets_available === false ? (zh ? ' · 未配置目标' : ' · No targets') : ''}</option>)}
                    </select>
                  </div>
                  {workbench.planning_taxonomy_options.find((item) => item.taxonomy_id === planningTaxonomyId)?.targets_available === false && (
                    <Link to={`/portfolios/${portfolioId}/taxonomies`}>{zh ? '运行优化前配置目标' : 'Configure targets before running optimization'}</Link>
                  )}
                </div>
                  <label>
                    <span>{zh ? '数据截止方式' : 'Data Cutoff Mode'}</span>
                    <select
                      value={asOfMode}
                      onChange={(event) => {
                        const nextMode = event.target.value as PortfolioResearchAsOfMode
                        const nextDate =
                          nextMode === 'dynamic'
                            ? workbench.as_of_date
                            : asOfDate || workbench.as_of_date
                        setAsOfMode(nextMode)
                        setAsOfDate(nextDate)
                        updateRunSetupDraft({ asOfMode: nextMode, asOfDate: nextDate })
                      }}
                    >
                      <option value="dynamic">{zh ? '最新可用数据' : 'Latest available data'}</option>
                      <option value="pinned">{zh ? '指定数据截止日' : 'Selected data cutoff'}</option>
                    </select>
                  </label>
                  <label>
                    <span>{zh ? '数据截止日' : 'Data Cutoff Date'}</span>
                    <input
                      type="date"
                      value={asOfDate}
                      disabled={asOfMode === 'dynamic'}
                      onChange={(event) => {
                        setAsOfDate(event.target.value)
                        updateRunSetupDraft({ asOfDate: event.target.value })
                      }}
                    />
                  </label>
                  <div className="research-parameter-group-title">{zh ? '组合求解' : 'Portfolio Solve'}</div>
                  <label>
                    <span>Capital Mode</span>
                    <select
                      value={capitalMode}
                      onChange={(event) => {
                        const nextCapitalMode = event.target.value as PortfolioResearchCapitalMode
                        const nextDraftUpdates: Partial<ResearchRunSetupDraft> = { capitalMode: nextCapitalMode }
                        if (nextCapitalMode === 'target_volatility' && !runSetupDraftRef.current.maxGrossExposure.trim()) {
                          nextDraftUpdates.maxGrossExposure = '1'
                          setMaxGrossExposure('1')
                        }
                        if (nextCapitalMode === 'volatility_cap') {
                          nextDraftUpdates.maxGrossExposure = ''
                          setMaxGrossExposure('')
                        }
                        setCapitalMode(nextCapitalMode)
                        updateRunSetupDraft(nextDraftUpdates)
                      }}
                    >
                      {CAPITAL_MODE_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>
                          {option.label}
                        </option>
                      ))}
                    </select>
                  </label>
                  {isVolatilityCapitalMode(capitalMode) ? (
                    <>
                      <label>
                        <span>{capitalMode === 'volatility_cap' ? 'Vol Cap (%)' : 'Target Vol (%)'}</span>
                        <input
                          type="number"
                          step="0.1"
                          min="0"
                          value={targetVolatilityPct}
                          onChange={(event) => {
                            setTargetVolatilityPct(event.target.value)
                            updateRunSetupDraft({ targetVolatilityPct: event.target.value })
                          }}
                          placeholder="7.0"
                        />
                      </label>
                    </>
                  ) : null}
                  {capitalMode === 'target_volatility' ? (
                    <>
                      <label>
                        <span>Max Gross</span>
                        <input
                          type="number"
                          step="0.01"
                          min="0"
                          value={maxGrossExposure}
                          onChange={(event) => {
                            setMaxGrossExposure(event.target.value)
                            updateRunSetupDraft({ maxGrossExposure: event.target.value })
                          }}
                          placeholder="1.00"
                        />
                      </label>
                    </>
                  ) : null}
                  {capitalMode === 'fixed_gross' ? (
                    <label>
                      <span>Gross Exposure</span>
                      <input
                        type="number"
                        step="0.01"
                        min="0"
                        value={grossExposure}
                        onChange={(event) => {
                          setGrossExposure(event.target.value)
                          updateRunSetupDraft({ grossExposure: event.target.value })
                        }}
                        placeholder="1.00"
                      />
                    </label>
                  ) : null}
                  <div className="research-freeze-field" ref={frozenMenuRef}>
                    <span>{zh ? '不交易分类' : 'No-trade Sleeves'} <InfoHint label={zh ? '不交易分类' : 'No-trade Sleeves'} detail={zh ? '保持当前持仓不交易；已纳入模型的证券仍参与协方差与风险贡献计算。' : 'No-trade fixes the current holding; modeled securities remain in covariance and risk contribution.'} /></span>
                    <button
                      type="button"
                      className="research-freeze-trigger"
                      onClick={() => setFrozenMenuOpen((current) => !current)}
                      disabled={!planningTaxonomyId || !selectableFrozenScopes.length || scopeOptionsLoading}
                      aria-haspopup="menu"
                      aria-expanded={frozenMenuOpen}
                    >
                      <span>{frozenMenuLabel}</span>
                      <span className="portfolio-select-caret" aria-hidden="true" />
                    </button>
                    {frozenMenuOpen ? (
                      <div className="research-freeze-menu" role="menu">
                        {selectableFrozenScopes.map((option) => {
                          const nodeId = option.taxonomy_node_id ?? ''
                          return (
                            <label key={nodeId} className="research-freeze-option">
                              <input
                                type="checkbox"
                                checked={frozenNodeIds.includes(nodeId)}
                                onChange={() => toggleFrozenNode(nodeId)}
                              />
                              <span>{option.label}</span>
                            </label>
                          )
                        })}
                      </div>
                    ) : null}
                    </div>
                    <div className="research-bounds-field" ref={boundsMenuRef}>
                      <span>Bounds</span>
                      <button
                        type="button"
                        className="research-freeze-trigger research-bounds-trigger"
                        onClick={() => setBoundsMenuOpen((current) => !current)}
                        disabled={!planningTaxonomyId || !topSleeveOptions.length || scopeOptionsLoading}
                        aria-haspopup="menu"
                        aria-expanded={boundsMenuOpen}
                      >
                        <span>{boundsMenuLabel}</span>
                        <span className="portfolio-select-caret" aria-hidden="true" />
                      </button>
                      {boundsMenuOpen ? (
                        <div className="research-bounds-menu" role="menu">
                          {topSleeveOptions.map((option) => {
                            const nodeId = option.taxonomy_node_id ?? ''
                            const bound = topSleeveBoundByNodeId.get(nodeId)
                            return (
                              <div key={nodeId} className="research-bounds-row">
                                <span>{option.label}</span>
                                <input
                                  type="number"
                                  step="0.1"
                                  min="0"
                                  max="100"
                                  value={bound?.minWeightPct ?? ''}
                                  onChange={(event) => updateTopSleeveBound(nodeId, 'minWeightPct', event.target.value)}
                                  placeholder="Min %"
                                />
                                <input
                                  type="number"
                                  step="0.1"
                                  min="0"
                                  max="100"
                                  value={bound?.maxWeightPct ?? ''}
                                  onChange={(event) => updateTopSleeveBound(nodeId, 'maxWeightPct', event.target.value)}
                                  placeholder="Max %"
                                />
                              </div>
                            )
                          })}
                        </div>
                      ) : null}
                    </div>
                  <div className="research-parameter-group-title">{zh ? '回测执行' : 'Backtest Execution'}</div>
                  <div className="research-benchmark-field">
                    <span>Benchmark</span>
                    <BenchmarkSearchBox
                      className="research-benchmark-search"
                      instruments={benchmarkInstruments}
                      selectedInstrumentId={benchmarkInstrumentId}
                      searchValue={benchmarkSearch}
                      onSearchChange={setBenchmarkSearch}
                      onSelectInstrument={(instrument) => {
                        setBenchmarkInstrumentId(instrument.instrument_id)
                        setBenchmarkSearch(benchmarkInstrumentLabel(instrument))
                        updateRunSetupDraft({ benchmarkInstrumentId: instrument.instrument_id })
                      }}
                      onClear={() => {
                        setBenchmarkInstrumentId('')
                        setBenchmarkSearch('')
                        updateRunSetupDraft({ benchmarkInstrumentId: '' })
                      }}
                      placeholder="Benchmark..."
                    />
                  </div>
                    <label>
                      <span>Rebalance</span>
                    <select
                      value={backtestRebalanceFrequency}
                      onChange={(event) => {
                        const nextFrequency = event.target.value as PortfolioResearchBacktestRebalanceFrequency
                        setBacktestRebalanceFrequency(nextFrequency)
                        updateRunSetupDraft({ backtestRebalanceFrequency: nextFrequency })
                      }}
                    >
                      {REBALANCE_OPTIONS.map((option) => (
                        <option key={option.value} value={option.value}>{option.label}</option>
                      ))}
                    </select>
                  </label>
                  <label>
                    <span>Cash Yield (%)</span>
                    <input
                      type="number"
                      step="0.1"
                      min="-100"
                      max="100"
                      value={cashYieldPct}
                      onChange={(event) => {
                        setCashYieldPct(event.target.value)
                        updateRunSetupDraft({ cashYieldPct: event.target.value })
                      }}
                    />
                  </label>
                  <label>
                    <span>Commission (bps)</span>
                    <input
                      type="number"
                      step="0.1"
                      min="0"
                      value={commissionBps}
                      onChange={(event) => {
                        setCommissionBps(event.target.value)
                        updateRunSetupDraft({ commissionBps: event.target.value })
                      }}
                    />
                  </label>
                  <label>
                    <span>Sell Tax (bps)</span>
                    <input
                      type="number"
                      step="0.1"
                      min="0"
                      value={taxBps}
                      onChange={(event) => {
                        setTaxBps(event.target.value)
                        updateRunSetupDraft({ taxBps: event.target.value })
                      }}
                    />
                  </label>
                  <label>
                    <span>Slippage (bps)</span>
                    <input
                      type="number"
                      step="0.1"
                      min="0"
                      value={slippageBps}
                      onChange={(event) => {
                        setSlippageBps(event.target.value)
                        updateRunSetupDraft({ slippageBps: event.target.value })
                      }}
                    />
                  </label>
                  <label>
                    <span>Delay (days)</span>
                    <input
                      type="number"
                      step="1"
                      min="0"
                      max="30"
                      value={implementationDelayDays}
                      onChange={(event) => {
                        setImplementationDelayDays(event.target.value)
                        updateRunSetupDraft({ implementationDelayDays: event.target.value })
                      }}
                    />
                  </label>

                </div>
                {scopeOptionsError ? <div className="inline-notice inline-notice-error">{scopeOptionsError}</div> : null}
              </fieldset>
              </div>
              <div className="research-parameters-actions">
                <button type="button" className="toolbar-link" disabled={actionPending === 'save'} onClick={closeSettings}>{canEditPortfolio ? (zh ? '取消' : 'Cancel') : (zh ? '关闭' : 'Close')}</button>
                {canEditPortfolio ? <button type="submit" className="toolbar-link button-primary" disabled={Boolean(actionPending) || scopeActionBlocked}>{actionPending === 'save' ? (zh ? '保存中…' : 'Saving…') : (zh ? '保存' : 'Save')}</button> : null}
              </div>
            </form>
            </div>
          </div> : null}

          {!latestRun ? (
            <section className="panel">
              <div className="empty-state">Run to generate the latest solved result.</div>
            </section>
          ) : latestRun.status === 'completed' && latestRun.detail == null ? (
            <section className="panel" aria-busy={runDetailLoading}>
              {runDetailLoading ? <CalculationStatus /> : (
                <div className="inline-notice inline-notice-error">
                  {runDetailError ?? 'Optimization result is unavailable.'}
                </div>
              )}
            </section>
          ) : latestRun.status === 'failed' ? (
            <section className="panel">
              <div className="inline-notice inline-notice-error">{latestRun.error_message ?? 'Optimization failed.'}</div>
            </section>
          ) : (
            <>
              <section className="panel research-result-panel" aria-label="Solved result">
              {staleRun ? (
                  <div
                    className="inline-notice inline-notice-warning"
                    role="status"
                    title={staleRunDetail}
                    aria-label={`Historical result — not current or execution-ready. ${staleRunDetail}`}
                    tabIndex={0}
                  >
                    <strong>Historical result — not current or execution-ready.</strong>
                  </div>
              ) : null}
              {nonExecutionReadySolveEvents.length ? (
                  <div
                    className="inline-notice inline-notice-warning"
                    role="status"
                    title={constrainedSolveDetail}
                    aria-label={`Constrained solve — not execution-ready. ${constrainedSolveDetail}`}
                    tabIndex={0}
                  >
                    <strong>Constrained solve — not execution-ready.</strong>
                  </div>
              ) : null}
                <ResearchSolutionTree run={staleRun ? { ...latestRun, reliability_state: 'stale', is_current: false } : latestRun} />
              </section>

              <ResearchComparisonPanel
                context={workbench.current_context}
                points={backtest?.points ?? []}
                endDate={latestRun.as_of_date}
                benchmarkPoints={displayBenchmark?.points ?? []}
                benchmarkLabel={displayBenchmark?.label}
                benchmarkLoading={benchmarkComparisonLoading}
                benchmarkError={benchmarkComparisonError}
                currentTargets={usesCurrentTargets}
                initialState={backtest?.initial_state}
                selectedScope={latestRun.detail?.selected_scope?.taxonomy_node_id || latestRun.detail?.risk_attribution_scope === 'selected_research_scope'
                  ? latestRun.detail?.selected_scope?.label ?? (zh ? '所选分类' : 'Selected category')
                  : null}
              />

              <ResearchSleeveCharts weightPoints={backtest?.top_sleeve_weight_points ?? []} contributionPoints={backtest?.top_sleeve_contribution_points ?? []} />

              <details className="panel research-evidence-disclosure">
                <summary>
                  <span>{zh ? '运行审计' : 'Run Audit'}</span>
                </summary>
                <div className="research-evidence-body">
                <div className="panel-header">
                  <div>
                    <div className="portfolio-detail-meta" data-testid="research-result-taxonomy">
                      {zh ? '结果分类：' : 'Result taxonomy: '}{latestRun.planning_taxonomy_name ?? latestRun.planning_taxonomy_id}
                      {changedTaxonomy ? (zh ? ' · 当前优化分类已改变，请重新运行。' : ' · The selected optimization taxonomy changed. Run Optimization again.') : ''}
                    </div>
                  </div>
                  <div className="portfolio-detail-meta">
                    {latestRun.as_of_date ?? '-'} · {staleRun ? 'Stale' : resolveStatusLabel(latestRun.status)} · {formatTimestamp(latestRun.finished_at)}
                  </div>
                </div>

                  <section className="portfolio-section-block">
                    <div className="panel-header panel-header-inline">
                      <div><div className="panel-title">Solver Diagnostics</div>
                        {globalSolverRun ? <div className="panel-subtitle">
                          {latestRun.detail?.risk_attribution_scope === 'selected_research_scope'
                            ? (zh ? '所选优化范围内统一求解；风险贡献相对于该范围。' : 'One global solve within the selected research scope; risk contributions are relative to that scope.')
                            : (zh ? '全组合统一求解；各层沿用自身的权重或风险预算依据。' : 'One portfolio-wide solve with each level retaining its weight or risk-budget basis.')}
                        </div> : null}
                      </div>
                    </div>
                    <HorizontalTableScroll className="table-shell">
                      <table className="transactions-table research-solver-diagnostics-table">
                        <thead>
                          <tr>
                            <th>Scope</th>
                            <th>{zh ? '配置依据' : 'Allocation Basis'}</th>
                            <th>Solver</th>
                            <th>Covariance</th>
                            <th>Observations</th>
                            <th>{zh ? '风险预算偏差' : 'Risk Budget Gap'}</th>
                            <th>Status</th>
                          </tr>
                        </thead>
                        <tbody>
                          {!solveEvents.length ? (
                            <TableStatusRow colSpan={7} label="No solver diagnostics were recorded." />
                          ) : solveEvents.map((event, index) => (
                            <tr key={`${event.scope_node_id ?? 'root'}:${index}`} title={event.solver_message ?? undefined}>
                              <td>{event.scope_path ?? event.scope_label}</td>
                              <td>{formatLabel(event.target_dimension ?? event.requested_target_dimension ?? 'N/A')}</td>
                              <td>{[event.solver_kind, event.solver_detail].filter(Boolean).map((value) => formatLabel(value ?? '')).join(' · ') || 'N/A'}</td>
                              <td>{event.covariance_model ? formatLabel(event.covariance_model) : 'N/A'}</td>
                              <td>{event.covariance_observations ?? 'N/A'}</td>
                              <td>{formatMaybePercent(event.max_risk_share_gap, 4)}</td>
                              <td>{event.execution_ready === false ? 'Review needed' : formatLabel(event.target_status ?? 'complete')}</td>
                            </tr>
                          ))}
                        </tbody>
                      </table>
                    </HorizontalTableScroll>
                  </section>

              <section className="performance-block-grid research-validation-grid">
                <div className="portfolio-section-block">
                  <div className="panel-header panel-header-inline">
                    <div><div className="panel-title">Historical Data Coverage</div></div>
                  </div>
                  <p className="section-caption">
                    FCN and options are no-trade, zero-return capital outside covariance and Risk Budget. Capital changes come only from recorded lifecycle dates; coupons, payoffs, credit risk, FX risk, collateral and liquidity remain unmodeled.
                  </p>
                  <p className="section-caption">
                    {usesCurrentTargets
                      ? 'The current taxonomy, targets and research eligibility are frozen for this run and used throughout its historical simulation. Market observations retain their decision-date cutoff. Delayed NAV publication and fund dealing restrictions are not simulated.'
                      : 'This archive retains the target rules recorded with the original run. Run Optimization again to use the current targets throughout the historical simulation.'}
                  </p>
                  <HorizontalTableScroll className="table-shell">
                    <table className="transactions-table research-validation-summary-table">
                      <tbody>
                        <tr>
                          <th>Method</th>
                          <td>{backtest?.methodology?.name ?? 'N/A'}</td>
                        </tr>
                        <tr>
                          <th>Status</th>
                          <td
                            title={pointInTimeCoverage?.unavailable_reason ?? undefined}
                            tabIndex={pointInTimeCoverage?.unavailable_reason ? 0 : undefined}
                          >
                            {pointInTimeCoverage ? formatLabel(pointInTimeCoverage.status) : 'N/A'}
                          </td>
                        </tr>
                        <tr>
                          <th>Decisions</th>
                          <td>{pointInTimeCoverage?.decision_count ?? 'N/A'}</td>
                        </tr>
                        <tr>
                          <th>Derivative Capital Events</th>
                          <td>{backtest ? backtest.derivative_capital_events?.length ?? 0 : 'N/A'}</td>
                        </tr>
                        <tr>
                          <th>Skipped Decisions</th>
                          <td
                            title={skippedRebalanceDetail || undefined}
                            tabIndex={skippedRebalanceDetail ? 0 : undefined}
                          >
                            {pointInTimeCoverage ? skippedRebalances.length : 'N/A'}
                          </td>
                        </tr>
                        <tr>
                          <th>Pending Decisions</th>
                          <td title={pendingRebalances.map((item) => item.reason).join('\n') || undefined}>
                            {pointInTimeCoverage ? pendingRebalances.length : 'N/A'}
                          </td>
                        </tr>
                        <tr>
                          <th>{usesCurrentTargets ? 'Target Snapshot Version' : 'Archived Configuration Versions'}</th>
                          <td>{configurationVersionsUsed.length
                            ? configurationVersionsUsed.join(', ')
                            : 'N/A'}</td>
                        </tr>
                        <tr>
                          <th>Decision Window</th>
                          <td>{pointInTimeCoverage?.first_decision_date && pointInTimeCoverage.last_decision_date
                            ? `${pointInTimeCoverage.first_decision_date} to ${pointInTimeCoverage.last_decision_date}`
                            : 'N/A'}</td>
                        </tr>
                        <tr>
                          <th>{zh ? '累计换手率' : 'Cumulative Turnover'}</th>
                          <td>{formatMaybePercent(backtest?.total_turnover)}</td>
                        </tr>
                        <tr>
                          <th>{zh ? '累计成本比例' : 'Cumulative Cost Ratio'} <InfoHint label={zh ? '成本口径' : 'Cost basis'} detail={zh ? '累计交易成本占回测期初 NAV 的比例。' : 'Cumulative trading costs divided by the simulation’s initial NAV.'} /></th>
                          <td>{formatMaybePercent(backtest?.total_cost)}</td>
                        </tr>
                        <tr>
                          <th>{zh ? '收益贡献对账差额' : 'Return Contribution Residual'}</th>
                          <td>{latestContributionReconciliation
                            ? formatMaybeNumber(latestContributionReconciliation.residual, 8)
                            : 'N/A'}</td>
                        </tr>
                      </tbody>
                    </table>
                  </HorizontalTableScroll>
                </div>
                <div className="portfolio-section-block">
                  <div className="panel-header panel-header-inline">
                    <div><div className="panel-title">Execution & Costs</div></div>
                  </div>
                  <HorizontalTableScroll className="table-shell">
                    <table className="transactions-table research-execution-table">
                      <thead>
                        <tr>
                          <th>Decision</th>
                          <th>Execution</th>
                          <th>Config</th>
                          <th>{zh ? '买入比例' : 'Buy Ratio'} <InfoHint label={zh ? '成交比例口径' : 'Trade ratio basis'} detail={zh ? '买入、卖出金额分别除以调仓前 NAV。' : 'Buy and sell amounts each divided by NAV before the rebalance.'} /></th>
                          <th>{zh ? '卖出比例' : 'Sell Ratio'}</th>
                          <th>{zh ? '衍生品权重' : 'Derivative Weight'} <InfoHint label={zh ? '权重口径' : 'Weight basis'} detail={zh ? '衍生品保持原账面金额；衍生品和现金权重均以执行后、已扣费的 NAV 为分母。' : 'Derivative carrying capital is held fixed. Derivative and cash weights use NAV after execution and costs.'} /></th>
                          <th>{zh ? '现金目标权重' : 'Target Cash Weight'}</th>
                          <th>{zh ? '单边换手率' : 'One-way Turnover'}</th>
                          <th>{zh ? '成本比例' : 'Cost Ratio'} <InfoHint label={zh ? '成本口径' : 'Cost basis'} detail={zh ? '本次交易成本占回测期初 NAV 的比例。' : 'This execution’s trading costs divided by the simulation’s initial NAV.'} /></th>
                        </tr>
                      </thead>
                      <tbody>
                        {!executionRecords.length ? (
                          <TableStatusRow colSpan={9} label="N/A" />
                        ) : executionRecords.map((record, index) => (
                          <tr key={`${record.decision_date}:${record.actual_execution_date}:${index}`}>
                            <td>{record.decision_date}</td>
                            <td>{record.actual_execution_date}</td>
                            <td>{record.taxonomy_configuration_version ?? 'N/A'}</td>
                            <td>{formatMaybePercent(record.risky_buy_turnover)}</td>
                            <td>{formatMaybePercent(record.risky_sell_turnover)}</td>
                            <td>{formatMaybePercent(record.derivative_target_weight)}</td>
                            <td>{formatMaybePercent(record.cash_target_weight)}</td>
                            <td>{formatMaybePercent(record.one_way_turnover)}</td>
                            <td>{formatMaybePercent(record.total_cost)}</td>
                          </tr>
                        ))}
                      </tbody>
                    </table>
                  </HorizontalTableScroll>
                </div>
              </section>


                </div>
              </details>
            </>
          )}
        </>
      ) : null}
    </PortfolioWorkspaceLayout>
  )
}
