/** Publication scope is shared; admission and actual reviewed work are separate. */
export default function ResearchScopeNotice({ instrumentId }: { instrumentId: string }) {
  return <aside className="research-scope-notice" aria-label="研究发布范围">
    <details><summary>共享研究范围与复核规则</summary>
    <p><strong>标的共享研究</strong> · <code translate="no">{instrumentId}</code></p>
    <p>主题和已发布成果属于同一标的，主题名称或所在列表不会隔离共享记录。是否执行研究取决于暂停状态与拟投或在投研究范围，保存主题不代表研究已开始。</p>
    <p>首次研究建立基线，后续按新增材料与条件变化选择复核范围。未复核主题保留原判断与日期；风险线索仍需独立评估。</p>
    </details>
  </aside>
}
