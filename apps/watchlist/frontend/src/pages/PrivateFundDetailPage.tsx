import FundDetailPage from './FundDetailPage'
import type { CorporateActionEvent } from '../lib/api'

type PrivateFundDetailPageProps = {
  fundId: string
  watchlistContext?: {
    watchlistId: string
    watchlistName?: string | null
    isSystem?: boolean
  } | null
  corporateActions?: CorporateActionEvent[]
}

export default function PrivateFundDetailPage(props: PrivateFundDetailPageProps) {
  return <FundDetailPage {...props} fundType="private_fund" />
}
