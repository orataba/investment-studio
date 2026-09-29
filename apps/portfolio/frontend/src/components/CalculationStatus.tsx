import { LoadingNotice } from '../../../../../packages/ui/src/NoticeToast'

type CalculationStatusProps = { label?: string }

export default function CalculationStatus({ label = 'Loading' }: CalculationStatusProps) {
  return <LoadingNotice active message={label} />
}
