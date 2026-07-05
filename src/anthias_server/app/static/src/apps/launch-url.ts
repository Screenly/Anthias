// Build a signage-app launch URL from its manifest's `launch.template`
// (an RFC 6570 URI Template) and the current setting values. Pure
// function — no DOM — so it can be unit tested and reused by both the
// add and edit config forms.
//
// Ported faithfully from the app store's expand-template.js (the store
// renders the same config forms from the same manifests), so a URL
// built here matches the one the store would build for identical
// values. Every signage-app manifest expresses its launch URL as a
// single form-style query expression, e.g. `{?name,tz,format}` or
// `{?location*,locale,24h}`. We implement exactly that operator:
// `{?var,var*,...}`. A trailing `*` explodes an array (repeated
// `name=` params) or an object (its `key=value` pairs).
//
// Only meaningful values reach the URL: a value that is undefined,
// empty, `false` or equal to its schema default is omitted (so the URL
// stays at the app's defaults until the user actually changes
// something). A boolean `true` becomes `name=1`. Slashes and the
// `zone|label` pipe are kept literal — matching the links apps
// document for themselves — while other reserved characters are
// percent-encoded (consumers decode them either way).

import type { SettingValue, SettingValues } from './types'

export type { SettingValue, SettingValues }

const encodeToken = (value: string): string =>
  encodeURIComponent(value).replace(/%2F/g, '/').replace(/%7C/g, '|')

// A scalar value we drop from the URL: nothing chosen, or still at the
// default.
function isEmpty(value: SettingValue, def: SettingValue): boolean {
  if (value === undefined || value === null || value === '' || value === false) {
    return true
  }
  return def !== undefined && value === def
}

// Expand one template variable spec (optionally `name*`) into
// `key=value` parts.
function expandVar(
  spec: string,
  values: SettingValues,
  defaults: SettingValues,
): string[] {
  const explode = spec.endsWith('*')
  const name = explode ? spec.slice(0, -1) : spec
  const value = values[name]

  if (explode) {
    // Omit an exploded array/object that is still at its (non-empty)
    // default.
    const def = defaults[name]
    if (def !== undefined && JSON.stringify(value) === JSON.stringify(def)) {
      return []
    }
    if (Array.isArray(value)) {
      return value
        .filter((v) => v !== undefined && v !== null && v !== '')
        .map((v) => `${name}=${encodeToken(String(v))}`)
    }
    if (value && typeof value === 'object') {
      return Object.entries(value)
        .filter(([, v]) => v !== undefined && v !== null && v !== '')
        .map(([k, v]) => `${encodeToken(k)}=${encodeToken(String(v))}`)
    }
    return []
  }

  if (isEmpty(value, defaults[name])) return []
  return [`${name}=${encodeToken(value === true ? '1' : String(value))}`]
}

export function buildLaunchUrl(
  baseUrl: string,
  template: string,
  values: SettingValues = {},
  defaults: SettingValues = {},
): string {
  if (!template) return baseUrl
  const match = template.match(/\{\?([^}]*)\}/)
  if (!match) return baseUrl

  const parts: string[] = []
  for (const spec of match[1]
    .split(',')
    .map((s) => s.trim())
    .filter(Boolean)) {
    parts.push(...expandVar(spec, values, defaults))
  }
  return parts.length ? `${baseUrl}?${parts.join('&')}` : baseUrl
}
