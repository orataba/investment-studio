/** All theme-triggered runs use the same shared instrument publication contract. */
export default function ResearchScopeNotice({ instrumentId }: { instrumentId: string }) {
  return <aside className="sector-research-limitation" aria-label="研究发布范围">
    <p><strong>整资产共享研究</strong> · <code translate="no">{instrumentId}</code></p>
    <p>提交将研究整个标的，并可能更新共享报告、事件和机会风险摘要，自动新增研究主题与待审风险线索。主题名称或所在列表不会隔离这些成果。</p>
    <p>活跃主题会进入后续研究；风险线索仍需独立评估。暂停主题只停止该主题的后续跟踪，不会中止当前任务、清除已有成果或停止其他研究与风险调度。</p>
  </aside>
}
