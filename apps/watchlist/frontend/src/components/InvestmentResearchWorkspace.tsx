import { useEffect, useState, type ReactNode } from 'react'

import {
  getInstrumentResearchHistory,
  type InstrumentResearchHistoryResponse,
  createInstrumentResearchNote,
  deleteInstrumentResearchNote,
  updateInstrumentResearchProfile,
  updateInstrumentResearchNote,
  type InstrumentResearchNote,
  type InstrumentResearchNoteInput,
  type InstrumentResearchNoteType,
  type InstrumentResearchProfileInput,
  type InstrumentResearchResponse,
} from '../lib/api'
import { Link } from 'react-router'
import { stageLabels, readWorkbench, type Topic } from '../lib/workbenchApi'
import { formatDate, formatDateTime, formatLabel } from '../lib/format'


export type ResearchInstrumentType =
  | 'public_fund'
  | 'private_fund'
  | 'etf'
  | 'equity'
  | 'index'
  | 'crypto'

type Props = {
  instrumentId: string
  instrumentType: ResearchInstrumentType
  research: InstrumentResearchResponse
  onChange: (research: InstrumentResearchResponse) => void
  language: 'en' | 'zh-Hans'
  defaultNoteDate?: string
  requestedNoteDate?: string | null
  onRequestedNoteHandled?: () => void
  onOpenNote?: (noteDate: string) => void
  children?: ReactNode
}

type NoteDraft = InstrumentResearchNoteInput & { note_id?: string }

type ResearchField = {
  key: keyof InstrumentResearchProfileInput
  label: string
  prompt: string
  rows?: number
}

const NOTE_TYPES: InstrumentResearchNoteType[] = [
  'research_update',
  'thesis_update',
  'evidence',
  'meeting',
  'event',
  'risk',
  'decision',
  'review',
]

function t(language: Props['language'], en: string, zh: string) {
  return language === 'zh-Hans' ? zh : en
}

function typeSpecificLabels(
  instrumentType: ResearchInstrumentType,
  language: Props['language'],
) {
  const english = {
    edge: 'Business Quality / Research Edge',
    valuation: 'Valuation Framework',
    people: 'Management & Governance Assessment',
  }
  const chinese = {
    edge: '商业质量 / 研究优势',
    valuation: '估值框架',
    people: '管理层与治理分析',
  }
  const values = language === 'zh-Hans' ? chinese : english
  if (instrumentType === 'crypto') {
    return language === 'zh-Hans'
      ? { edge: '网络、供给机制与采用', valuation: '流动性与定价框架', people: '协议治理、托管与市场结构' }
      : { edge: 'Network, Supply & Adoption', valuation: 'Liquidity & Pricing Framework', people: 'Protocol Governance, Custody & Market Structure' }
  }
  if (instrumentType === 'public_fund') {
    return language === 'zh-Hans'
      ? { edge: '投资流程与优势', valuation: '配置与估值考虑', people: '基金经理与投研团队分析' }
      : { edge: 'Process & Investment Edge', valuation: 'Allocation / Valuation Considerations', people: 'Manager & Research Team Assessment' }
  }
  if (instrumentType === 'private_fund') {
    return language === 'zh-Hans'
      ? { edge: '策略、流程与优势', valuation: '入场与估值考虑', people: '关键人物、机构与治理分析' }
      : { edge: 'Strategy, Process & Edge', valuation: 'Entry / Valuation Considerations', people: 'Key Person, Organization & Governance Assessment' }
  }
  if (instrumentType === 'etf') {
    return language === 'zh-Hans'
      ? { edge: '产品、指数与实施质量', valuation: '估值、折溢价与交易框架', people: '管理人、做市与产品治理分析' }
      : { edge: 'Product, Index & Implementation Quality', valuation: 'Valuation, Premium / Discount & Trading Frame', people: 'Sponsor, Market Making & Product Governance Assessment' }
  }
  if (instrumentType === 'index') {
    return language === 'zh-Hans'
      ? { edge: '指数构建与方法论质量', valuation: '指数估值背景', people: '指数提供方与方法论治理分析' }
      : { edge: 'Index Construction & Methodology Quality', valuation: 'Index Valuation Context', people: 'Provider & Methodology Governance Assessment' }
  }
  return values
}

function researchSections(
  instrumentType: ResearchInstrumentType,
  language: Props['language'],
): Array<{ title: string; fields: ResearchField[] }> {
  const labels = typeSpecificLabels(instrumentType, language)
  const sections: Array<{ title: string; fields: ResearchField[] }> = [
    {
      title: t(language, 'Investment Case', '投资逻辑'),
      fields: [
        {
          key: 'thesis',
          label: t(language, 'Investment Thesis', '核心投资论点'),
          prompt: t(language, 'What must be true for this investment to work?', '这项投资成立必须满足什么？'),
          rows: 5,
        },
        {
          key: 'current_view',
          label: t(language, 'Current View', '当前判断'),
          prompt: t(language, 'State the current conclusion, not a data summary.', '写明当前结论，而不是复述数据。'),
        },
        {
          key: 'why_now',
          label: t(language, 'Why Now', '为什么是现在'),
          prompt: t(language, 'What makes the opportunity timely?', '现在值得关注或行动的原因是什么？'),
        },
        {
          key: 'edge_assessment',
          label: labels.edge,
          prompt: t(language, 'Where is the durable quality or edge, and how is it evidenced?', '持续的质量或优势在哪里，证据是什么？'),
        },
        {
          key: 'valuation_framework',
          label: labels.valuation,
          prompt: t(language, 'Record the valuation or entry framework and key assumptions.', '记录估值或入场框架及关键假设。'),
        },
        {
          key: 'catalysts',
          label: t(language, 'Catalysts / Triggers', '催化剂 / 触发因素'),
          prompt: t(language, 'What could change market perception or unlock value?', '什么可能改变市场认知或兑现价值？'),
        },
      ],
    },
    {
      title: t(language, 'Risk & Falsification', '风险与证伪'),
      fields: [
        {
          key: 'key_risks',
          label: t(language, 'Key Risks', '关键风险'),
          prompt: t(language, 'Focus on causal risks, not generic disclaimers.', '记录有因果意义的风险，不写泛泛免责声明。'),
        },
        {
          key: 'disconfirming_evidence',
          label: t(language, 'Thesis Breakers / Disconfirming Evidence', '论点失效条件 / 反证'),
          prompt: t(language, 'What evidence would make us reduce conviction or exit?', '什么证据会让我们降低信心或退出？'),
        },
        {
          key: 'open_questions',
          label: t(language, 'Open Questions / Evidence Gaps', '待解决问题 / 证据缺口'),
          prompt: t(language, 'What remains unknown or unverified?', '还有哪些未知或未经验证？'),
        },
        {
          key: 'monitoring_plan',
          label: t(language, 'Monitoring Plan', '跟踪计划'),
          prompt: t(language, 'Which facts, events, or people should be checked next?', '下一步需要核查哪些事实、事件或人物？'),
        },
      ],
    },
    {
      title: t(language, 'People & Decision', '人物分析与投资决策'),
      fields: [
        {
          key: 'people_assessment',
          label: labels.people,
          prompt: t(language, 'Record judgment, track record, incentives, and observed behavior—not only biographies.', '记录判断、历史表现、激励和实际行为，而不只是人物简历。'),
          rows: 5,
        },
        {
          key: 'portfolio_role',
          label: t(language, 'Portfolio Role', '组合角色'),
          prompt: t(language, 'What job should this investment perform in the portfolio?', '它在组合里应该承担什么职责？'),
        },
        {
          key: 'time_horizon',
          label: t(language, 'Time Horizon', '投资期限'),
          prompt: t(language, 'Expected holding and thesis realization horizon.', '预期持有期及论点兑现时间。'),
        },
        {
          key: 'decision_rationale',
          label: t(language, 'Decision Rationale', '决策依据'),
          prompt: t(language, 'Explain the action or inaction implied by the current evidence.', '说明当前证据为何支持行动或不行动。'),
        },
      ],
    },
  ]
  const primaryKeys = ['thesis', 'current_view', 'key_risks', 'monitoring_plan']
  const all = sections.flatMap(section => section.fields)
  const primary = primaryKeys.map(key => all.find(field => field.key === key)!)
  primary[0] = { ...primary[0], label: t(language, 'Why follow this investment?', '为什么关注它'), rows: 3 }
  primary[1] = { ...primary[1], label: t(language, 'Current view and reason for change', '当前判断与变化原因') }
  primary[2] = { ...primary[2], label: t(language, 'Risks and thesis breakers', '关键风险与失效条件') }
  primary[3] = { ...primary[3], label: t(language, 'Next research steps', '下一步研究与复核') }
  if (instrumentType === 'private_fund') {
    primary[0].prompt = '收益由什么驱动？来自市场敞口、选股、交易还是承担流动性风险？在组合中希望补足什么？'
    primary[2].prompt = '何种市场环境会失效？关注拥挤、杠杆、容量、净值平滑、开放与赎回约束。'
    primary[3].prompt = '下次需要向管理人核查哪些敞口、风险事件或策略变化？'
  } else if (instrumentType === 'public_fund') {
    primary[0].prompt = '与哪个基准或同类产品比较？超额收益来自什么，是否依赖风格或规模？'
    primary[2].prompt = '关注风格漂移、超额回撤、基金经理变化、费用及同类持仓重合。'
  }

  return [{ title: t(language, 'Investment questions', '投资问题'), fields: primary }, ...sections.map(section => ({ ...section, fields: section.fields.filter(field => !primaryKeys.includes(field.key)) }))]
}


function localIsoDate() {
  const now = new Date()
  return new Date(now.getTime() - now.getTimezoneOffset() * 60_000)
    .toISOString()
    .slice(0, 10)
}

function emptyNoteDraft(defaultDate: string, defaultAuthor: string): NoteDraft {
  return {
    note_date: defaultDate || localIsoDate(),
    note_type: 'research_update',
    title: '',
    summary: '',
    body: '',
    importance: 'medium',
    tags: [],
    source_refs: '',
    people: '',
    author: defaultAuthor,
    follow_up_date: null,
  }
}

function noteDraftFromRecord(note: InstrumentResearchNote): NoteDraft {
  return {
    note_id: note.note_id,
    note_date: note.note_date,
    note_type: note.note_type,
    title: note.title,
    summary: note.summary,
    body: note.body,
    importance: note.importance,
    tags: note.tags,
    source_refs: note.source_refs,
    people: note.people,
    author: note.author,
    follow_up_date: note.follow_up_date,
    completed_at: note.completed_at,
  }
}

function profileInput(research: InstrumentResearchResponse): InstrumentResearchProfileInput {
  const {
    created_at: _createdAt,
    updated_at: _updatedAt,
    updated_by: _updatedBy,
    revision_number: _revisionNumber,
    ...profile
  } = research.profile
  return profile
}

function displayValue(value: unknown, language: Props['language']) {
  return typeof value === 'string' && value.trim()
    ? value
    : t(language, 'Not recorded', '尚未记录')
}

export default function InvestmentResearchWorkspace({
  instrumentId,
  instrumentType,
  research,
  onChange,
  language,
  requestedNoteDate = null,
  onRequestedNoteHandled,
  onOpenNote,
  children,
}: Props) {
  const [profileDraft, setProfileDraft] = useState(() => profileInput(research))
  const [editingProfile, setEditingProfile] = useState(false)
  const [noteDraft, setNoteDraft] = useState<NoteDraft | null>(null)
  const [tagsText, setTagsText] = useState('')
  const [saving, setSaving] = useState<'profile' | 'note' | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [relatedTopics, setRelatedTopics] = useState<Topic[]>([])
  useEffect(() => { readWorkbench<Topic[]>(`/research/topics?instrument_id=${encodeURIComponent(instrumentId)}`).then(setRelatedTopics).catch(error => setError(error.message)) }, [instrumentId])
  const [history, setHistory] = useState<InstrumentResearchHistoryResponse | null>(null)
  const [noteSearch, setNoteSearch] = useState('')
  const [noteFilter, setNoteFilter] = useState('')
  const [notice, setNotice] = useState<string | null>(null)
  const sections = researchSections(instrumentType, language)
  const isFund = instrumentType === 'public_fund' || instrumentType === 'private_fund'

  useEffect(() => {
    if (!editingProfile) setProfileDraft(profileInput(research))
  }, [editingProfile, instrumentId, research])

  useEffect(() => {
    if (!requestedNoteDate) return
    setNoteDraft(emptyNoteDraft(requestedNoteDate, research.profile.primary_analyst))
    setTagsText('')
    setError(null)
    setNotice(null)
    onRequestedNoteHandled?.()
  }, [onRequestedNoteHandled, requestedNoteDate, research.profile.primary_analyst])

  function beginProfileEdit() {
    setProfileDraft(profileInput(research))
    setEditingProfile(true)
    setError(null)
    setNotice(null)
  }

  function cancelProfileEdit() {
    setProfileDraft(profileInput(research))
    setEditingProfile(false)
    setError(null)
  }

  async function saveProfile() {
    setSaving('profile')
    setError(null)
    setNotice(null)
    try {
      const response = await updateInstrumentResearchProfile(instrumentId, {
        profile: Object.fromEntries(
          Object.entries(profileDraft).map(([key, value]) => [
            key,
            typeof value === 'string' ? value.trim() : value,
          ]),
        ) as InstrumentResearchProfileInput,
        updated_by: 'terminal_ui',
      })
      onChange(response)
      setEditingProfile(false)
      setNotice(t(language, 'Investment view saved.', '投资判断已保存。'))
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : t(language, 'Failed to save investment view.', '保存投资判断失败。'))
    } finally {
      setSaving(null)
    }
  }

  function beginNewNote() {
    setNoteDraft(emptyNoteDraft(localIsoDate(), research.profile.primary_analyst))
    setTagsText('')
    setError(null)
    setNotice(null)
  }

  function beginEditNote(note: InstrumentResearchNote) {
    setNoteDraft(noteDraftFromRecord(note))
    setTagsText(note.tags.join(', '))
    setError(null)
    setNotice(null)
  }

  async function saveNote() {
    if (!noteDraft) return
    if (!noteDraft.title.trim()) {
      setError(t(language, 'Research notes require a title.', '研究记录必须填写标题。'))
      return
    }
    setSaving('note')
    setError(null)
    setNotice(null)
    const { note_id: noteId, ...input } = noteDraft
    const note = {
      ...input,
      title: input.title.trim(),
      summary: input.summary.trim(),
      body: input.body.trim(),
      source_refs: input.source_refs.trim(),
      people: input.people.trim(),
      author: input.author.trim(),
      tags: tagsText.split(',').map((value) => value.trim()).filter(Boolean),
    }
    try {
      const response = noteId
        ? await updateInstrumentResearchNote(instrumentId, noteId, { note, updated_by: 'terminal_ui' })
        : await createInstrumentResearchNote(instrumentId, { note, updated_by: 'terminal_ui' })
      onChange(response)
      setNoteDraft(null)
      setTagsText('')
      setNotice(t(language, 'Research record saved.', '研究记录已保存。'))
    } catch (saveError) {
      setError(saveError instanceof Error ? saveError.message : t(language, 'Failed to save research record.', '保存研究记录失败。'))
    } finally {
      setSaving(null)
    }
  }

  async function deleteNote(noteId: string) {
    setSaving('note')
    setError(null)
    setNotice(null)
    try {
      const response = await deleteInstrumentResearchNote(instrumentId, noteId)
      onChange(response)
      setNoteDraft((current) => (current?.note_id === noteId ? null : current))
      setNotice(t(language, 'Research record deleted.', '研究记录已删除。'))
    } catch (deleteError) {
      setError(deleteError instanceof Error ? deleteError.message : t(language, 'Failed to delete research record.', '删除研究记录失败。'))
    } finally {
      setSaving(null)
    }
  }

  return (
    <div className="investment-research-workspace">
      {error ? <div className="error-state" role="alert">{error}</div> : null}
      {notice ? <div className="inline-notice inline-notice-success" role="status">{notice}</div> : null}

      <section className="instrument-research-section investment-research-profile">
        <div className="instrument-research-section-header">
          <div>
            <div className="panel-title">{t(language, 'Investment Research', '投资研究')}</div>
            <div className="instrument-section-title">{t(language, 'Current Investment View', '当前投资判断')}</div>
          </div>
          <div className="toolbar">
            {editingProfile ? (
              <>
                <button type="button" onClick={cancelProfileEdit} disabled={saving === 'profile'}>
                  {t(language, 'Cancel', '取消')}
                </button>
                <button type="button" className="button-primary" onClick={() => void saveProfile()} disabled={saving === 'profile'}>
                  {saving === 'profile' ? t(language, 'Saving...', '保存中...') : t(language, 'Save View', '保存判断')}
                </button>
              </>
            ) : (
              <button type="button" onClick={beginProfileEdit}>{t(language, 'Edit View', '编辑判断')}</button>
            )}
          </div>
        </div>

        <details><summary>{t(language, "Previous conviction rating", "历史信心评分")}</summary>
        <div className="investment-research-rating-row">
          <div>
            <span>{t(language, 'Conviction Rating', '投资信心评分')}</span>
            <small>{t(language, '1 = Avoid · 3 = Watch · 5 = High conviction', '1 = 回避 · 3 = 观察 · 5 = 高信心')}</small>
          </div>
          <div className="instrument-manual-rating-picker" role="radiogroup" aria-label="Investment conviction rating">
            {[1, 2, 3, 4, 5].map((rating) => {
              const active = profileDraft.manual_rating != null && rating <= profileDraft.manual_rating
              return (
                <button
                  key={rating}
                  type="button"
                  role="radio"
                  aria-checked={profileDraft.manual_rating === rating}
                  disabled={!editingProfile}
                  className={active ? 'instrument-manual-rating-star instrument-manual-rating-star-active' : 'instrument-manual-rating-star'}
                  onClick={() => setProfileDraft((current) => ({
                    ...current,
                    manual_rating: current.manual_rating === rating ? null : rating,
                  }))}
                >
                  {active ? '★' : '☆'}
                </button>
              )
            })}
          </div>
        </div>

        </details>
        <label className="investment-research-owner-field"><span>{t(language, 'Research stage', '研究阶段')}</span><select disabled={!editingProfile} value={profileDraft.research_stage || 'watching'} onChange={event => setProfileDraft(current => ({ ...current, research_stage: event.target.value }))}>{Object.entries(stageLabels).map(([value, label]) => <option key={value} value={value}>{language === 'zh-Hans' ? label : value}</option>)}</select></label>

        <div className="investment-research-owner-grid">
          {([
            ['primary_analyst', t(language, 'Primary Analyst', '主要分析人'), 'text'],
            ['next_review_date', t(language, 'Next Review', '下次复核日期'), 'date'],
          ] as const).map(([key, label, inputType]) => (
            <label key={key} className="investment-research-owner-field">
              <span>{label}</span>
              {editingProfile ? (
                <input
                  type={inputType}
                  value={profileDraft[key] || ''}
                  onChange={(event) => setProfileDraft((current) => ({ ...current, [key]: event.target.value || (key === 'next_review_date' ? null : '') }))}
                />
              ) : (
                <strong>{key === 'next_review_date' && profileDraft[key] ? formatDate(profileDraft[key]) : displayValue(profileDraft[key], language)}</strong>
              )}
            </label>
          ))}
          <div className="investment-research-owner-field">
            <span>{t(language, 'Last Updated', '最近更新')}</span>
            <strong>{research.profile.updated_at ? formatDateTime(research.profile.updated_at) : t(language, 'Not saved', '尚未保存')}</strong>
          </div>
          <div className="investment-research-owner-field">
            <span>{t(language, 'View Revision', '观点版本')}</span>
            <strong>{research.profile.revision_number ? `v${research.profile.revision_number}` : '—'}</strong>
          </div>
        </div>

        {sections.map((section, index) => (
          <details open={index === 0 ? true : undefined} key={section.title} className="investment-research-case-section">
            <summary>{section.title}</summary>
            <div className="investment-research-case-grid">
              {section.fields.map((field) => (
                <label key={field.key} className={`investment-research-case-field${field.rows && field.rows > 4 ? ' investment-research-case-field-wide' : ''}`}>
                  <span>{field.label}</span>
                  {editingProfile ? (
                    <>
                      <textarea
                        rows={field.rows || 3}
                        value={String(profileDraft[field.key] || '')}
                        onChange={(event) => setProfileDraft((current) => ({ ...current, [field.key]: event.target.value }))}
                      />
                      <small>{field.prompt}</small>
                    </>
                  ) : (
                    <p className={profileDraft[field.key] ? '' : 'investment-research-empty-value'}>
                      {displayValue(profileDraft[field.key], language)}
                    </p>
                  )}
                </label>
              ))}
            </div>
          </details>
        ))}

        {isFund ? (
          <div className="investment-research-case-section">
            <details><summary>{t(language, 'Previous diligence labels', '历史尽调标签')}</summary>
            <div className="investment-research-owner-grid investment-research-fund-status-grid">
              {([
                ['dd_status', t(language, 'Investment diligence', '投资尽调')],
                ['odd_status', t(language, 'Operational diligence', '运营尽调')],
                ['ic_status', t(language, 'Investment review', '投资审核')],
              ] as const).map(([key, label]) => (
                <label key={key} className="investment-research-owner-field">
                  <span>{label}</span>
                  {editingProfile ? (
                    <input value={profileDraft[key]} onChange={(event) => setProfileDraft((current) => ({ ...current, [key]: event.target.value }))} />
                  ) : (
                    <strong>{displayValue(profileDraft[key], language)}</strong>
                  )}
                </label>
              ))}
            </div></details>
          </div>
        ) : null}
      </section>

      <details><summary>{t(language, 'Additional attributes and historical labels', '补充属性与历史标签')}</summary>{children}</details>
      <div className="toolbar"><Link to={`/assistant?instruments=${encodeURIComponent(instrumentId)}`}>{t(language, 'Ask research assistant', '向助手提问')}</Link><button onClick={() => void getInstrumentResearchHistory(instrumentId).then(setHistory).catch(error => setError(error.message))}>{t(language, 'View revision history', '查看观点与记录历史')}</button></div>
      <div className="research-scope">{relatedTopics.map(topic => <Link key={topic.topic_id} to={`/assistant?topic=${topic.topic_id}`}>{topic.title}</Link>)}</div>
      {history && <details open><summary>{t(language, 'View history', '观点历史')}</summary>{history.profile_revisions.map(revision => <article className="research-history-row" key={revision.revision_number}><small>v{revision.revision_number} · {formatDateTime(revision.recorded_at)}</small><p>{revision.current_view || revision.thesis || '—'}</p><details><summary>{t(language, 'Full snapshot', '完整快照')}</summary><pre className="research-evidence-json">{JSON.stringify(revision, null, 2)}</pre></details></article>)}<details><summary>{t(language, 'Record changes', '记录修改历史（含删除）')}</summary><pre className="research-evidence-json">{JSON.stringify(history.note_revisions, null, 2)}</pre></details></details>}


      <section className="instrument-research-section investment-research-log">
        <div className="instrument-research-section-header">
          <div>
            <div className="panel-title">{t(language, 'Research Record', '研究记录')}</div>
            <div className="instrument-section-title">{t(language, 'Evidence, Meetings, Decisions & Reviews', '证据、访谈、决策与复盘')}</div>
          </div>
          <div className="toolbar">
            <button type="button" onClick={beginNewNote}>{t(language, 'Add Record', '新增记录')}</button>
          </div>
        </div>

        {noteDraft ? (
          <div className="investment-research-note-editor">
            <div className="investment-research-note-editor-header">
              <strong>{noteDraft.note_id ? t(language, 'Edit Research Record', '编辑研究记录') : t(language, 'New Research Record', '新建研究记录')}</strong>
              <div className="toolbar">
                <button type="button" onClick={() => setNoteDraft(null)} disabled={saving === 'note'}>{t(language, 'Cancel', '取消')}</button>
                <button type="button" className="button-primary" onClick={() => void saveNote()} disabled={saving === 'note'}>
                  {saving === 'note' ? t(language, 'Saving...', '保存中...') : t(language, 'Save Record', '保存记录')}
                </button>
              </div>
            </div>
            <div className="investment-research-note-form-grid">
              <label className="investment-research-note-field-wide"><span>{t(language, 'Title', '标题')}</span><input value={noteDraft.title} onChange={(event) => setNoteDraft((current) => current ? { ...current, title: event.target.value } : current)} /></label>
              <label className="investment-research-note-field-wide"><span>{t(language, 'Analysis', '分析内容')}</span><textarea rows={5} value={noteDraft.body} onChange={(event) => setNoteDraft((current) => current ? { ...current, body: event.target.value } : current)} /></label>
            </div>
            <details><summary>{t(language, "Date, sources and follow-up", "日期、来源与跟进（选填）")}</summary><div className="investment-research-note-form-grid">
              <label><span>{t(language, 'Date', '日期')}</span><input type="date" value={noteDraft.note_date} onChange={(event) => setNoteDraft((current) => current ? { ...current, note_date: event.target.value } : current)} /></label>
              <label><span>{t(language, 'Type', '类型')}</span><select value={noteDraft.note_type} onChange={(event) => setNoteDraft((current) => current ? { ...current, note_type: event.target.value as InstrumentResearchNoteType } : current)}>{NOTE_TYPES.map((type) => <option key={type} value={type}>{formatLabel(type)}</option>)}</select></label>
              <label><span>{t(language, 'Importance', '重要性')}</span><select value={noteDraft.importance} onChange={(event) => setNoteDraft((current) => current ? { ...current, importance: event.target.value as NoteDraft['importance'] } : current)}><option value="low">{t(language, "Low", "低")}</option><option value="medium">{t(language, "Medium", "中")}</option><option value="high">{t(language, "High", "高")}</option></select></label>
              <label><span>{t(language, 'Author / Analyst', '作者 / 分析人')}</span><input value={noteDraft.author} onChange={(event) => setNoteDraft((current) => current ? { ...current, author: event.target.value } : current)} /></label>
              <label className="investment-research-note-field-wide"><span>{t(language, 'Summary / Conclusion', '摘要 / 结论')}</span><textarea rows={2} value={noteDraft.summary} onChange={(event) => setNoteDraft((current) => current ? { ...current, summary: event.target.value } : current)} /></label>
              <label><span>{t(language, 'People Discussed / Met', '涉及 / 访谈人物')}</span><input value={noteDraft.people} onChange={(event) => setNoteDraft((current) => current ? { ...current, people: event.target.value } : current)} /></label>
              <label><span>{t(language, 'Follow-up Date', '后续跟进日期')}</span><input type="date" value={noteDraft.follow_up_date || ''} onChange={(event) => setNoteDraft((current) => current ? { ...current, follow_up_date: event.target.value || null } : current)} /></label>
              <label className="investment-research-note-field-wide"><span>{t(language, 'Evidence / Source References', '证据 / 来源引用')}</span><textarea rows={2} value={noteDraft.source_refs} onChange={(event) => setNoteDraft((current) => current ? { ...current, source_refs: event.target.value } : current)} /></label>
              <label className="investment-research-note-field-wide"><span>{t(language, 'Tags', '标签')}</span><input value={tagsText} placeholder={t(language, 'Comma-separated', '使用逗号分隔')} onChange={(event) => setTagsText(event.target.value)} /></label>
            </div></details>
          </div>
        ) : null}

        <div className="toolbar"><input aria-label="搜索研究记录" placeholder={t(language, 'Search records', '搜索研究记录')} value={noteSearch} onChange={event => setNoteSearch(event.target.value)} /><select aria-label="记录类型" value={noteFilter} onChange={event => setNoteFilter(event.target.value)}><option value="">{t(language, 'All records', '全部记录')}</option>{NOTE_TYPES.map(type => <option key={type} value={type}>{formatLabel(type)}</option>)}</select></div>
        {research.notes.length ? (
          <div className="investment-research-note-list">
            {research.notes.filter(note => (!noteFilter || note.note_type === noteFilter) && `${note.title} ${note.body} ${note.summary}`.toLowerCase().includes(noteSearch.toLowerCase())).map((note) => (
              <article key={note.note_id} className="investment-research-note-card">
                <div className="investment-research-note-card-header">
                  <div>
                    <div className="investment-research-note-meta">
                      <span>{formatDate(note.note_date)}</span>
                      <span>{formatLabel(note.note_type)}</span>
                      <span className={`investment-research-importance investment-research-importance-${note.importance}`}>{formatLabel(note.importance)}</span>
                    </div>
                    <h3 translate="no">{note.title}</h3>
                  </div>
                  <div className="instrument-table-inline-actions instrument-table-inline-actions-compact">
                    <button className="table-action" disabled={saving === 'profile'} onClick={() => { setProfileDraft({ ...profileInput(research), current_view: note.summary || note.body, decision_rationale: `${note.note_date} · ${note.title}` }); setEditingProfile(true); }}>{t(language, 'Use in current view', '整理为当前观点')}</button>
                    {note.follow_up_date ? <button className="table-action" disabled={saving === 'note'} onClick={() => { setSaving('note'); const { note_id: _noteId, ...input } = noteDraftFromRecord(note); void updateInstrumentResearchNote(instrumentId, note.note_id, { note: { ...input, completed_at: note.completed_at ? null : new Date().toISOString() }, updated_by: 'terminal_ui' }).then(onChange).catch(error => setError(error.message)).finally(() => setSaving(null)); }}>{note.completed_at ? t(language, 'Reopen follow-up', '重新打开跟进') : t(language, 'Complete follow-up', '完成跟进')}</button> : null}
                    {onOpenNote ? <button type="button" className="table-action" onClick={() => onOpenNote(note.note_date)}>{t(language, 'Open in Chart', '在图表中打开')}</button> : null}
                    <button type="button" className="table-action" onClick={() => beginEditNote(note)}>{t(language, 'Edit', '编辑')}</button>
                    <button type="button" className="table-action" disabled={saving === 'note'} onClick={() => void deleteNote(note.note_id)}>{t(language, 'Delete', '删除')}</button>
                  </div>
                </div>
                {note.summary ? <p className="investment-research-note-summary" translate="no">{note.summary}</p> : null}
                {note.body ? <p className="investment-research-note-body" translate="no">{note.body}</p> : null}
                <dl className="investment-research-note-details">
                  {note.author ? <><dt>{t(language, 'Author', '作者')}</dt><dd translate="no">{note.author}</dd></> : null}
                  {note.people ? <><dt>{t(language, 'People', '人物')}</dt><dd translate="no">{note.people}</dd></> : null}
                  {note.source_refs ? <><dt>{t(language, 'Evidence', '证据')}</dt><dd translate="no">{note.source_refs}</dd></> : null}
                  {note.follow_up_date ? <><dt>{t(language, 'Follow-up', '跟进')}</dt><dd>{formatDate(note.follow_up_date)} {note.completed_at ? t(language, "Completed", "已完成") : t(language, "Pending", "待跟进")}</dd></> : null}
                </dl>
                {note.tags.length ? <div className="investment-research-note-tags" translate="no">{note.tags.map((tag) => <span key={tag}>{tag}</span>)}</div> : null}
                <div className="investment-research-note-audit">
                  {t(language, 'Saved', '保存于')} {formatDateTime(note.updated_at)} · v{note.revision_number}
                </div>
              </article>
            ))}
          </div>
        ) : (
          <div className="instrument-placeholder instrument-research-placeholder">
            {t(language, 'No research records yet. Add evidence, meetings, decisions, and reviews as the thesis evolves.', '暂无研究记录。随着投资论点演进，请记录证据、访谈、决策与复盘。')}
          </div>
        )}
      </section>
    </div>
  )
}
