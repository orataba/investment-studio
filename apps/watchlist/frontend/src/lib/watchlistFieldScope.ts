export type InstrumentScopedField = {
  instrument_scope_json: string[]
}

export function fieldSupportsAllInstrumentTypes(
  field: InstrumentScopedField,
  instrumentTypes: string[],
) {
  if (!field.instrument_scope_json.length) return true
  if (!instrumentTypes.length) return false

  const scope = new Set(
    field.instrument_scope_json.map((value) => String(value).trim().toLowerCase()),
  )
  return instrumentTypes.every((value) => scope.has(String(value).trim().toLowerCase()))
}
