import { useId, useState, type CSSProperties, type KeyboardEvent } from 'react'
import HorizontalTableScroll from '../../../../../packages/ui/src/HorizontalTableScroll'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { formatNumber } from '../lib/format'
import type { CorrelationMatrix } from '../lib/riskCorrelation'
import type { RiskWindowDiagnostics } from '../lib/riskWindowData'

function heatmapCellStyle(value: number | null | undefined, maxAbs: number): CSSProperties {
  if (value == null || Number.isNaN(value) || maxAbs <= 0) {
    return {}
  }
  const intensity = Math.min(1, Math.max(0.08, Math.abs(value) / maxAbs))
  if (value < 0) {
    return { backgroundColor: `color-mix(in srgb, var(--studio-chart-secondary) ${(0.06 + intensity * 0.24) * 100}%, white)` }
  }
  return { backgroundColor: `color-mix(in srgb, var(--studio-chart-primary) ${(0.06 + intensity * 0.24) * 100}%, white)` }
}

function formatCorrelation(value: number | null | undefined) {
  if (value == null || Number.isNaN(value)) {
    return '—'
  }
  return formatNumber(value, 2)
}

export default function CorrelationMatrixView({ matrix, emptyLabel, diagnostics }: { matrix: CorrelationMatrix; emptyLabel: string; diagnostics?: RiskWindowDiagnostics }) {
  const { language } = useLanguage()
  const zh = language === 'zh-Hans'
  const [activeKey, setActiveKey] = useState<string | null>(null)
  const [pair, setPair] = useState<[string, string] | null>(null)
  const detailId = useId()
  if (!matrix.groups.length) return <div className="price-chart-empty">{emptyLabel}</div>
  const active = matrix.groups.find((group) => group.key === activeKey)
  // Resolve identity again after sorting/scope changes, never retain an old row index.
  const rowIndex = Math.max(0, matrix.groups.findIndex(group => group.key === pair?.[0]))
  const columnIndex = Math.max(0, matrix.groups.findIndex(group => group.key === pair?.[1]))
  const selectedRow = matrix.groups[rowIndex]
  const selectedColumn = matrix.groups[columnIndex]
  const selectedCell = matrix.cells[rowIndex]?.[columnIndex]
  const displayName = (key: string, label: string) => key.startsWith('unassigned:') ? (zh ? '未归类' : 'Unassigned') : label
  const selectPair = (row: number, column: number) => {
    setActiveKey(null)
    setPair([matrix.groups[row].key, matrix.groups[column].key])
  }
  function movePair(event: KeyboardEvent<HTMLButtonElement>, row: number, column: number) {
    const moves: Record<string, [number, number]> = { ArrowUp: [-1, 0], ArrowDown: [1, 0], ArrowLeft: [0, -1], ArrowRight: [0, 1] }
    const move = moves[event.key]
    if (!move && event.key !== 'Home' && event.key !== 'End') return
    event.preventDefault()
    const last = matrix.groups.length - 1
    const nextRow = Math.min(last, Math.max(0, row + (move?.[0] ?? 0)))
    const nextColumn = event.key === 'Home' ? 0 : event.key === 'End' ? last : Math.min(last, Math.max(0, column + move![1]))
    const table = event.currentTarget.closest('table')
    selectPair(nextRow, nextColumn)
    table?.querySelector<HTMLButtonElement>(`[data-pair="${nextRow}:${nextColumn}"]`)?.focus()
  }
  const nameButton = (key: string, label: string) => <button type="button" className="risk-matrix-identity" translate="no"
    title={displayName(key, label)} aria-label={displayName(key, label)} onFocus={() => setActiveKey(key)} onClick={() => setActiveKey(key)}>{displayName(key, label)}</button>
  return <div>
    <div className="risk-matrix-caption">
      <span className="risk-matrix-identity-detail" aria-live="polite" translate="no">{active ? displayName(active.key, active.label) : ''}</span>
      <span className="risk-correlation-scale" aria-label={zh ? '色阶固定为负一到正一' : 'Fixed color scale from minus one to plus one'}>−1 <i /> +1</span>
    </div>
    <div id={detailId} className="risk-matrix-pair-detail" role="status" aria-live="polite">
      <strong translate="no">{displayName(selectedRow.key, selectedRow.label)} × {displayName(selectedColumn.key, selectedColumn.label)}</strong>
      <span>{zh ? '相关系数' : 'Correlation'}: <b>{formatCorrelation(selectedCell?.value)}</b> · {selectedCell?.observationCount ?? 0} {zh ? '个共同观测' : 'common observations'}</span>
      <span>{zh ? '实际样本期间' : 'Observed sample period'}: <span translate="no">{diagnostics?.periodStartDate ?? diagnostics?.observedStartDate ?? '—'} → {diagnostics?.observedEndDate ?? '—'}</span></span>
      <small>{zh ? '选择数值查看资产对；方向键移动，Home / End 定位本行。' : 'Select a value to inspect its pair; arrow keys move, Home / End select the row edges.'}</small>
    </div>
    <HorizontalTableScroll className="risk-matrix-scroll risk-covariance-scroll" aria-label={zh ? '相关矩阵' : 'Correlation matrix'}>
      <table className="risk-heatmap-table risk-covariance-table" style={{ width: `${240 + matrix.groups.length * 120}px` }}>
        <colgroup><col className="risk-matrix-label-col" />{matrix.groups.map((group) => <col key={group.key} className="risk-matrix-value-col" />)}</colgroup>
        <thead><tr><th className="risk-matrix-corner">{zh ? '名称' : 'Name'}</th>{matrix.groups.map((group) =>
          <th scope="col" className="risk-matrix-column-header" key={group.key} data-active={group.key === activeKey || group.key === selectedColumn.key}>{nameButton(group.key, group.label)}</th>)}</tr></thead>
        <tbody>{matrix.groups.map((row, rowIndex) => <tr key={row.key}>
          <th scope="row" className="risk-matrix-row-header" data-active={row.key === activeKey || row.key === selectedRow.key}>{nameButton(row.key, row.label)}</th>
          {matrix.groups.map((column, columnIndex) => {
            const cell = matrix.cells[rowIndex]?.[columnIndex]
            const selected = row.key === selectedRow.key && column.key === selectedColumn.key
            const title = `${displayName(row.key, row.label)} × ${displayName(column.key, column.label)} · ${cell?.observationCount ?? 0} ${zh ? '个共同观测' : 'common observations'}`
            return <td key={column.key} className="risk-heatmap-cell" style={heatmapCellStyle(cell?.value, matrix.maxAbs)}
              data-pair-axis={row.key === selectedRow.key || column.key === selectedColumn.key}>
              <button type="button" className="risk-matrix-cell-button" data-pair={`${rowIndex}:${columnIndex}`}
                title={title} aria-label={`${title} · ${formatCorrelation(cell?.value)}`} aria-pressed={selected} aria-describedby={detailId}
                tabIndex={selected ? 0 : -1} onFocus={() => selectPair(rowIndex, columnIndex)} onClick={() => selectPair(rowIndex, columnIndex)}
                onKeyDown={event => movePair(event, rowIndex, columnIndex)}>{formatCorrelation(cell?.value)}</button>
            </td>
          })}
        </tr>)}</tbody>
      </table>
    </HorizontalTableScroll>
  </div>
}
