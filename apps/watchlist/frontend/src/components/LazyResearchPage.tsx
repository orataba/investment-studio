import { lazy, Suspense, type ComponentProps } from 'react'
import LoadingOverlay from './LoadingOverlay'
import WorkspaceLoadingDrawer from '../../../../../packages/ui/src/WorkspaceLoadingDrawer'

const ResearchPage = lazy(() => import('../pages/ResearchPage'))

export default function LazyResearchPage(props: ComponentProps<typeof ResearchPage>) {
  return <Suspense fallback={props.onClose ? <WorkspaceLoadingDrawer kind="assistant" onClose={props.onClose} /> : <LoadingOverlay />}><ResearchPage {...props} /></Suspense>
}
