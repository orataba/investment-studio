import FundDetailPage from './FundDetailPage'
import type { CorporateActionEvent } from '../lib/api'

type PublicFundDetailPageProps = {
  fundId: string
  watchlistContext?: {
    watchlistId: string
    watchlistName?: string | null
  } | null
  corporateActions?: CorporateActionEvent[]
}

export default function PublicFundDetailPage(props: PublicFundDetailPageProps) {
  return <FundDetailPage {...props} fundType="public_fund" />
}
