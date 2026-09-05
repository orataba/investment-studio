export default function WorkspaceToolIcon({
  kind,
}: {
  kind: 'risk' | 'assistant'
}) {
  return (
    <svg
      viewBox="0 0 24 24"
      width="20"
      height="20"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
    >
      {kind === 'risk' ? (
        <>
          <path d="M12 3 4.5 6v5c0 4.4 3 7.8 7.5 10 4.5-2.2 7.5-5.6 7.5-10V6Z" />
          <path d="M12 8v5m0 3h.01" />
        </>
      ) : (
        <>
          <path d="M19.5 13.5V16a2 2 0 0 1-2 2H9l-5 3V6a2 2 0 0 1 2-2h7" />
          <path d="m18 2 1.4 3.6L23 7l-3.6 1.4L18 12l-1.4-3.6L13 7l3.6-1.4ZM8 11h3m-3 3h7" />
        </>
      )}
    </svg>
  )
}
