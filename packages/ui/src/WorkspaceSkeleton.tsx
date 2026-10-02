import { LoadingNotice } from './NoticeToast'
import './notice-toast.css'
import './workspace-skeleton.css'

export default function WorkspaceSkeleton() {
  return <div className="workspace-skeleton">
    <LoadingNotice active message="Loading" compact />
    <div className="workspace-skeleton-lines" aria-hidden="true"><i /><i /><i /></div>
  </div>
}
