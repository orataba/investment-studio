import type { HoldingsOperationalAlert } from '../lib/api'

export default function HoldingsOperationalStatus({
  alerts,
}: {
  alerts: readonly HoldingsOperationalAlert[] | null | undefined
}) {
  if (!alerts?.length) {
    return null
  }

  return (
    <section
      className="holdings-operational-status"
      aria-labelledby="holdings-operational-status-heading"
    >
      <div className="holdings-operational-status-heading">
        <h2 id="holdings-operational-status-heading">Operational Status</h2>
        <span>{alerts.length}</span>
      </div>
      <ul>
        {alerts.map((alert) => (
          <li
            key={alert.code}
            className={`holdings-operational-alert holdings-operational-alert-${alert.severity}`}
          >
            <strong>{alert.title}</strong>
            <span>{alert.message}</span>
          </li>
        ))}
      </ul>
    </section>
  )
}
