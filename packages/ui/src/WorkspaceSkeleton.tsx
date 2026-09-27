import './workspace-skeleton.css'

export default function WorkspaceSkeleton() {
  return <div className="workspace-skeleton" role="status" aria-busy="true" aria-label="Loading">
    <span>Loading</span>
    <i aria-hidden="true" /><i aria-hidden="true" /><i aria-hidden="true" />
  </div>
}
