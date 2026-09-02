type InfoHintProps = {
  label: string
  detail: string
  tone?: 'info' | 'warning'
}

export default function InfoHint({ label, detail, tone = 'info' }: InfoHintProps) {
  return (
    <span
      className={`portfolio-info-hint portfolio-info-hint-${tone}`}
      role="note"
      aria-label={`${label}: ${detail}`}
      title={detail}
      tabIndex={0}
    >
      <span aria-hidden="true">{tone === 'warning' ? '!' : 'i'}</span>
    </span>
  )
}
