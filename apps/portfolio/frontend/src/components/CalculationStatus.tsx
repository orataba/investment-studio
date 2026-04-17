type CalculationStatusProps = {
  label?: string
}

export default function CalculationStatus({
  label = 'Recalculating…',
}: CalculationStatusProps) {
  return (
    <div className="calculation-status" role="status" aria-live="polite">
      <span className="calculation-status-spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  )
}
