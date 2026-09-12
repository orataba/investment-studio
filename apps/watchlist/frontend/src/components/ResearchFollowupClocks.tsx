import { dateLabel } from './ResearchEvidence'

export default function ResearchFollowupClocks({ changedAt, reviewedAt, reviewStatus }: {
  changedAt?: string | null; reviewedAt?: string | null; reviewStatus?: string | null
}) {
  return <p className="research-followup-clocks sector-research-note">
    {changedAt && <span>最近实质进展 <time dateTime={changedAt}>{dateLabel(changedAt)}</time></span>}
    <span>{reviewStatus === 'insufficient_evidence' ? '当前判断最近复核证据不足' : '当前判断最近复核'} {reviewedAt ? <time dateTime={reviewedAt}>{dateLabel(reviewedAt)}</time> : '尚未记录'}</span>
  </p>
}
