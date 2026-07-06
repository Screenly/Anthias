import type { SettingSchema, SettingValue } from './types'

// The visible text a <select> shows for one option value: an explicit
// `x-enumLabels` entry (honoured even when empty, matching the option
// the operator sees), else 'Default' for the empty value, else the raw
// value stringified. Shared by the form renderer (the <option> text)
// and the asset-name derivation so a name derived from a chosen option
// always matches the label rendered for it.
export function selectOptionLabel(
  schema: SettingSchema,
  value: SettingValue,
): string {
  const labels = schema['x-enumLabels'] || []
  const options = schema.enum ?? []
  const i = options.findIndex((v) => v === value)
  if (i >= 0 && labels[i] !== undefined) return labels[i]
  return value === '' ? 'Default' : String(value)
}
