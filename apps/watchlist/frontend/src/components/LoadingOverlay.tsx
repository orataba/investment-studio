type LoadingOverlayProps = {
  label?: string
}

export default function LoadingOverlay({ label = 'Loading' }: LoadingOverlayProps) {
  return (
    <div className="watchlist-loading-overlay" role="status" aria-live="polite">
      <span className="watchlist-loading-spinner" aria-hidden="true" />
      <span>{label}</span>
    </div>
  )
}
