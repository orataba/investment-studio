type InstrumentWithLifecycle = {
  lifecycle_state: {
    status: string
  }
}

export function instrumentsForVisibility<T extends InstrumentWithLifecycle>(
  instruments: readonly T[],
  showInactive: boolean,
) {
  return showInactive
    ? [...instruments]
    : instruments.filter((instrument) => instrument.lifecycle_state.status === 'active')
}

export function detailForSelection<T extends { instrument_id: string }>(
  detail: T | null,
  selectedInstrumentId: string,
) {
  return detail?.instrument_id === selectedInstrumentId ? detail : null
}
