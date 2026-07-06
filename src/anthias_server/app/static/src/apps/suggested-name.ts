import { selectOptionLabel } from './select-label'
import type { AppManifest, SettingValue } from './types'

// Derive a distinguishing default asset name from an app's config.
// Several store apps are one generic app configured per install via a
// labelled select — e.g. the RSS Reader picks a feed — so every install
// would otherwise share the identical manifest name ("RSS Reader"). The
// chosen option's label (the feed's title) is exactly what tells two
// installs apart, so prefer it. Falls back to the manifest name for apps
// with no such setting (or before one is chosen). Uses the first select
// with `x-enumLabels` in manifest order, so it's generic, not
// RSS-specific, and resolves the label exactly as the select renderer
// does (`selectOptionLabel`) so the derived name matches what the
// operator sees selected.
export function suggestedName(
  manifest: AppManifest,
  values: Record<string, SettingValue>,
): string {
  const props = manifest.settings?.properties ?? {}
  for (const [key, schema] of Object.entries(props)) {
    const options = schema.enum
    if (!schema['x-enumLabels'] || !options) continue
    const value = key in values ? values[key] : (schema.default as SettingValue)
    // Only derive from a select whose current value is one of its
    // options; otherwise selectOptionLabel would stringify a stray
    // value into a bogus name.
    if (!options.includes(value)) continue
    const label = selectOptionLabel(schema, value)
    // An empty resolved label (an explicit empty `x-enumLabels` entry)
    // can't name a required field — fall through to the next select.
    if (label) return label
  }
  return manifest.name
}
