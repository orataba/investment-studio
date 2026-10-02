import { useState, type CSSProperties } from 'react'
import HorizontalTableScroll from '../../../../../packages/ui/src/HorizontalTableScroll'
import { useLanguage } from '../../../../../packages/ui/src/i18n'
import { formatNumber } from '../lib/format'
import type { CorrelationMatrix } from '../lib/riskCorrelation'

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

export default function CorrelationMatrixView({ matrix, emptyLabel }: { matrix: CorrelationMatrix; emptyLabel: string }) {
  const { language } = useLanguage()
  const zh = language === 'zh-Hans'
  const [activeKey, setActiveKey] = useState<string | null>(null)
  if (!matrix.groups.length) return <div className="price-chart-empty">{emptyLabel}</div>
  const active = matrix.groups.find((group) => group.key === activeKey)
  const displayName = (key: string, label: string) => key.startsWith('unassigned:') ? (zh ? '未归类' : 'Unassigned') : label
  const nameButton = (key: string, label: string) => <button type="button" className="risk-matrix-identity" translate="no"
    title={displayName(key, label)} aria-label={displayName(key, label)} onFocus={() => setActiveKey(key)} onClick={() => setActiveKey(key)}>{displayName(key, label)}</button>
  return <div>
    <div className="risk-matrix-caption">
      <span className="risk-matrix-identity-detail" aria-live="polite" translate="no">{active ? displayName(active.key, active.label) : ''}</span>
      <span className="risk-correlation-scale" aria-label={zh ? '色阶固定为负一到正一' : 'Fixed color scale from minus one to plus one'}>−1 <i /> +1</span>
    </div>
    <HorizontalTableScroll className="risk-matrix-scroll risk-covariance-scroll" aria-label={zh ? '相关矩阵' : 'Correlation matrix'}>
      <table className="risk-heatmap-table risk-covariance-table" style={{ width: `${240 + matrix.groups.length * 120}px` }}>
        <colgroup><col className="risk-matrix-label-col" />{matrix.groups.map((group) => <col key={group.key} className="risk-matrix-value-col" />)}</colgroup>
        <thead><tr><th className="risk-matrix-corner">{zh ? '名称' : 'Name'}</th>{matrix.groups.map((group) =>
          <th scope="col" className="risk-matrix-column-header" key={group.key} data-active={group.key === activeKey}>{nameButton(group.key, group.label)}</th>)}</tr></thead>
        <tbody>{matrix.groups.map((row, rowIndex) => <tr key={row.key}>
          <th scope="row" className="risk-matrix-row-header" data-active={row.key === activeKey}>{nameButton(row.key, row.label)}</th>
          {matrix.groups.map((column, columnIndex) => {
            const cell = matrix.cells[rowIndex]?.[columnIndex]
            return <td key={column.key} className="risk-heatmap-cell" style={heatmapCellStyle(cell?.value, matrix.maxAbs)}
              title={`${displayName(row.key, row.label)} × ${displayName(column.key, column.label)} · ${cell?.observationCount ?? 0} ${zh ? '个共同观测' : 'common observations'}`}>{formatCorrelation(cell?.value)}</td>
          })}
        </tr>)}</tbody>
      </table>
    </HorizontalTableScroll>
  </div>
}
