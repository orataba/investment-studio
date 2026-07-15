import type { ButtonHTMLAttributes } from 'react'

import {
  supportsNavHistoryImport,
  type InstrumentType,
} from '../../../../packages/instrument-core/ts/src'

type NavImportButtonProps = ButtonHTMLAttributes<HTMLButtonElement> & {
  instrumentType: InstrumentType
}

export function NavImportButton({
  instrumentType,
  children = 'Import NAV',
  ...buttonProps
}: NavImportButtonProps) {
  if (!supportsNavHistoryImport(instrumentType)) {
    return null
  }
  return <button {...buttonProps}>{children}</button>
}
