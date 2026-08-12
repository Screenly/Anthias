// Compose one array-item row into a single string token per its item
// schema's `x-format` template (e.g. `{zone}|{label}`), and split a
// stored token back into a row for the edit form.
//
// `applyItemFormat` is a faithful port of the app store's
// assets/js/lib/item-format.js, so a launch URL built here is
// byte-identical to the one the store builds for the same rows. A blank
// field drops itself *and* the separator that joins it to its
// neighbour, so a World Clock row with only a zone yields `Europe/Oslo`
// rather than `Europe/Oslo|`. See the store's docs/app-manifest.md
// (`x-format`).
//
// `parseItemToken` has no counterpart in the store: the store only ever
// composes a fresh link, while Anthias saves the composed tokens in
// `metadata.app.values` and has to repopulate the rows when an operator
// reopens an installed app. Dropping a field *and* its separator is
// lossy, so this direction is a best effort — see the note above
// `chooseSlots`.

import type { SettingSchema } from './types'

export interface FormatField {
  name: string
  // Literal text between the previous field and this one. On the first
  // field this is the template's leading literal, which is a prefix
  // rather than a separator.
  sep: string
}

export interface ParsedFormat {
  fields: FormatField[]
  prefix: string
  tail: string
}

// Split an `x-format` template into its interpolated fields and the
// literals around them.
export function parseFormat(fmt: string): ParsedFormat {
  const re = /\{([^}]+)\}/g
  const fields: FormatField[] = []
  let last = 0
  let m: RegExpExecArray | null
  while ((m = re.exec(fmt)) !== null) {
    fields.push({ sep: fmt.slice(last, m.index), name: m[1] as string })
    last = re.lastIndex
  }
  return {
    fields,
    prefix: fields.length ? (fields[0] as FormatField).sep : '',
    tail: fmt.slice(last),
  }
}

// Field names in template order; `['value']` for a bare item schema with
// no sub-properties, matching the single unnamed input we render for it.
export function fieldNames(item: SettingSchema): string[] {
  const props = Object.keys(item.properties || {})
  if (props.length) return props
  const fmt = item['x-format']
  if (fmt) {
    const names = parseFormat(fmt).fields.map((f) => f.name)
    if (names.length) return names
  }
  return ['value']
}

// Compose a row into its token. Returns '' when every field is blank, so
// the caller can drop the row from the URL entirely.
export function applyItemFormat(
  fmt: string,
  item: Record<string, string> = {},
): string {
  const { fields, prefix, tail } = parseFormat(fmt)
  let out = ''
  let any = false
  for (const { sep, name } of fields) {
    const raw = item[name]
    const value = raw === undefined || raw === null ? '' : String(raw).trim()
    // Drop this field and the separator that would have joined it.
    if (!value) continue
    // The first emitted field takes no separator: field[0]'s `sep` is
    // the prefix (added below), and a later first-present field drops
    // the separator to its absent predecessor.
    out += (any ? sep : '') + value
    any = true
  }
  return any ? prefix + out + tail : ''
}

const escapeRe = (s: string): string =>
  s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')

// Which fields the parts of a short token belong to.
//
// A token with fewer parts than the format has fields is genuinely
// ambiguous, because the composer drops a blank field together with its
// separator: menu-board's `Coffee|Espresso` is a section and an item,
// while `Cortado|3.10` is an item and its price, and the two arrive in
// exactly the same shape. Nothing generic can tell them apart — the app
// itself resolves it with a domain heuristic (does the field contain a
// digit?) that only makes sense for menus.
//
// So we keep the leftmost fields and drop optional ones from the right,
// never dropping a field the item schema marks `required`. That reads
// the common shapes correctly (a lone `Espresso` is the required item
// name, not a section; `Coffee|Espresso` is section + item) and, because
// the parts are reassigned to a contiguous run of fields, re-composing
// an untouched row reproduces the original token exactly — so reopening
// and saving an app without editing it never changes its launch URL.
function chooseSlots(
  names: string[],
  count: number,
  required: string[],
): string[] {
  if (count >= names.length) return names
  const req = new Set(required)
  const kept = [...names]
  for (let i = kept.length - 1; i >= 0 && kept.length > count; i--) {
    if (!req.has(kept[i] as string)) kept.splice(i, 1)
  }
  // More required fields than parts — nothing better than the leftmost.
  return kept.length > count ? kept.slice(0, count) : kept
}

// Split a stored token back into a row keyed by field name. Every field
// is present in the result (blank when the token doesn't reach it) so
// the caller can render one input per field.
export function parseItemToken(
  fmt: string,
  token: string,
  required: string[] = [],
): Record<string, string> {
  const { fields, prefix, tail } = parseFormat(fmt)
  const names = fields.map((f) => f.name)
  const row: Record<string, string> = {}
  for (const name of names) row[name] = ''

  let rest = token
  if (prefix && rest.startsWith(prefix)) rest = rest.slice(prefix.length)
  if (tail && rest.endsWith(tail)) rest = rest.slice(0, -tail.length)
  if (!rest) return row

  // Separators can in principle differ per field, but a dropped field
  // takes its separator with it, so there is no reliable order to match
  // them in. Splitting on any of them is the best available reading and
  // is exact for the single-separator formats manifests actually use.
  const seps = [
    ...new Set(
      fields
        .slice(1)
        .map((f) => f.sep)
        .filter(Boolean),
    ),
  ]
  const parts = seps.length
    ? rest.split(new RegExp(seps.map(escapeRe).join('|')))
    : [rest]

  const slots = chooseSlots(names, parts.length, required)
  const lastSep = (fields[fields.length - 1] as FormatField | undefined)?.sep
  slots.forEach((name, i) => {
    // A value containing the separator itself over-splits; fold the
    // surplus back into the final field so the token still round-trips.
    const value =
      i === slots.length - 1 && parts.length > slots.length
        ? parts.slice(i).join(lastSep || '')
        : (parts[i] ?? '')
    row[name] = value.trim()
  })
  return row
}
