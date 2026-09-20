import { lazy, Suspense, type ComponentProps } from 'react'
import LoadingOverlay from './LoadingOverlay'

const ResearchPage = lazy(() => import('../pages/ResearchPage'))

export default function LazyResearchPage(props: ComponentProps<typeof ResearchPage>) {
  return <Suspense fallback={<LoadingOverlay />}><ResearchPage {...props} /></Suspense>
}
