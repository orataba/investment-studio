export default function LoadingOverlay() {
  return (
    <div className="watchlist-loading-overlay" role="status" aria-live="polite" aria-busy="true">
      <span className="watchlist-loading-spinner" aria-hidden="true" />
      <span>Loading</span>
    </div>
  )
}
