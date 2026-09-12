import type { LanguageMessages, LanguagePatternMessages } from '../../../../packages/ui/src/i18n'

export const researchMessages: LanguageMessages = { en: {
  '本轮复核': 'Review in this run', '已复核既有判断': 'Prior judgments reviewed', '复核证据不足': 'Insufficient evidence for review',
  '问题进展': 'Question update', '观察日程': 'Research calendar',
  '展开分析': 'Expand analysis', '收起分析': 'Collapse analysis', '来源观点': 'Source opinion',
  '研究动态': 'Research activity', '判断依据与期限': 'Assessment, evidence and horizon',
  '更新类型': 'Update type', '全部类型': 'All types', '研究判断': 'Research assessment', '预测': 'Forecast', '复盘': 'Review', '研究经验': 'Research lesson', '主题': 'Theme', '事件': 'Event', '简讯': 'Brief',
  '暂不跟进': 'No follow-up needed', '继续跟进': 'Following up', '已结束跟进': 'Follow-up completed',
  '人工判断': 'Human judgment', '研究员记录': 'Researcher record', '未标注作者': 'Author not recorded',
  '已撤回 · 仅供追溯': 'Withdrawn · historical record', '已修订 · 当时版本': 'Superseded · earlier version',
  '查看当时记录': 'Read the earlier record', '分析与研究依据': 'Analysis and evidence', '事件发生': 'Event occurred', '信息发布': 'Information published',
  '追问这条更新': 'Discuss this update', '讨论当时判断': 'Discuss the earlier assessment', '写投资观点': 'Write an investment opinion',
  '请助手建立主题': 'Ask assistant to create a theme', '我的投资判断': 'My investment judgment', '交给助手保存': 'Send to assistant to save',
  '将交给研究助手保存，并关联这条更新；助手完成保存后会显示结果。': 'The assistant will save your opinion with a reference to this update and report the result.',
  '按研究形成时间排列；事件发生和信息发布日期分别保留。主题中的更新与这里是同一条记录。': 'Ordered by when research was recorded. Event and publication dates are shown separately. Themes share these same records.',
  '当前范围没有研究更新。检查是否完成及资料覆盖情况，请查看上方研究状态。': 'No research updates in this range. Check the research status above for completion and coverage.',
  '正在读取研究动态…': 'Loading research activity…', '研究动态暂时无法读取：': 'Research activity is unavailable: ',
  '研究员提出': 'Proposed by researcher', '人工建立': 'Created by a team member', '当前判断': 'Current assessment', '最新变化': 'Latest development',
  '尚待形成研究判断。': 'The research assessment is pending.', '下一步观察': 'What to watch next', '结束原因': 'Reason for closing',
  '尚无主题更新。后续事件、判断和复盘会保留在这里。': 'No theme updates yet. Events, assessments and reviews will be retained here.',
  '背景与主题管理': 'Background and theme management', '当前判断依据': 'Evidence for the current assessment', '保存并结束': 'Save and close',
  '研究日程与结果': 'Research calendar and outcomes', '支持依据': 'Supporting evidence', '反向证据': 'Counterevidence', '机制复核': 'Mechanism review',
  '其他解释': 'Alternative explanations', '适用限制': 'Limitations', '判断期限': 'Assessment horizon', '观察条件': 'Observation conditions', '失效条件': 'Invalidation conditions',
  '关键假设': 'Key assumptions', '主题背景': 'Theme background', '资料限制': 'Coverage limitations', '撤回原因': 'Reason for withdrawal', '复核条件': 'Review conditions', '复盘结果': 'Review outcome', '经验': 'Lesson',
} }
export const researchPatterns: LanguagePatternMessages = { en: [
  { match: /^主题研究时间线 · (\d+)$/, replace: 'Theme timeline · $1' },
  { match: /^显示已修订或撤回的记录 · (\d+)$/, replace: 'Include superseded or withdrawn records · $1' },
] }
