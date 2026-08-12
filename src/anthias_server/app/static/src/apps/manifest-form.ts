// Generic, manifest-driven settings form.
//
// Reads a signage-app manifest's `settings` JSON Schema and renders the
// form controls — one per property, in manifest order — reporting each
// change back through `onChange(values)`. No app re-implements its own
// form: every manifest-driven app shares this code, exactly as the app
// store does (this is a port of its config-manifest.js, adapted to
// Anthias's modal styling and wired to a callback instead of writing an
// <input> directly).
//
// Supported `x-widget`s (falling back to the JSON Schema type, or a
// string `format`): text, url, number, datetime/date/time (from a
// `date-time`/`date`/`time` format), select (enum), toggle (boolean),
// timezone, and location-map (a {lat,lng} object). An `array` property
// renders as a repeated group of rows (Menu Board's items, World
// Clock's cities), each composed into one token by the item schema's
// `x-format`. Unknown widgets degrade to a text input; unsupported
// structural types (non-location objects) are skipped rather than
// mis-rendered.

import { applyItemFormat, fieldNames, parseItemToken } from './item-format'
import { initLocationMap } from './location-map'
import { selectOptionLabel } from './select-label'
import type { SettingSchema, SettingValue } from './types'
import { widgetFor } from './widget-for'

type SetFn = (key: string, value: SettingValue) => void

type HostWithCleanups = HTMLElement & { __cfgCleanups?: Array<() => void> }

// Run and clear any teardown callbacks a prior render stored on the
// host (Leaflet maps + their ResizeObservers), then empty it. Use this
// instead of a bare replaceChildren() anywhere the config host is
// cleared, so those maps don't leak.
export function teardownHost(host: HTMLElement): void {
  const h = host as HostWithCleanups
  h.__cfgCleanups?.forEach((fn) => {
    try {
      fn()
    } catch {
      /* teardown is best-effort */
    }
  })
  h.__cfgCleanups = []
  host.replaceChildren()
}

// A labelled wrapper shared by every control. When the control is a
// real form element we bind a <label for>; custom controls (e.g. the
// location map) have no focusable input, so we use a plain caption and
// wire aria-labelledby.
function fieldRow(
  schema: SettingSchema,
  key: string,
  control: HTMLElement,
  labelFor?: string,
): HTMLElement {
  const row = document.createElement('div')
  row.className = 'app-cfg-field'

  const captionText = schema.title || key
  let caption: HTMLElement
  if (labelFor) {
    const label = document.createElement('label')
    label.htmlFor = labelFor
    caption = label
  } else {
    caption = document.createElement('span')
    const captionId = `app-cfg-lbl-${key}`
    caption.id = captionId
    control.setAttribute('role', 'group')
    control.setAttribute('aria-labelledby', captionId)
  }
  caption.className = 'app-cfg-label'
  caption.textContent = captionText
  row.append(caption, control)

  if (schema.description) {
    const help = document.createElement('p')
    help.className = 'app-cfg-help'
    help.textContent = schema.description
    row.appendChild(help)
  }
  return row
}

// Monotonic id source so each rendered form's timezone <datalist> gets
// a globally-unique id — the Add-app and Edit-app config hosts both
// live in the modal DOM, so a shared hard-coded id would produce
// duplicate ids and an <input list=…> could bind to the wrong host's
// list.
let tzListSeq = 0

// One shared <datalist> of IANA time zones per host (looked up by a
// data-attribute, not the global id), created lazily when the browser
// can enumerate zones.
function timezoneList(host: HTMLElement): HTMLDataListElement | null {
  const existing = host.querySelector<HTMLDataListElement>('[data-tz-list]')
  if (existing) return existing
  const intl = Intl as typeof Intl & {
    supportedValuesOf?: (key: string) => string[]
  }
  const zones =
    typeof intl.supportedValuesOf === 'function'
      ? intl.supportedValuesOf('timeZone')
      : []
  if (!zones.length) return null
  const list = document.createElement('datalist')
  list.id = `app-cfg-tz-list-${tzListSeq++}`
  list.setAttribute('data-tz-list', '')
  for (const zone of zones) {
    const opt = document.createElement('option')
    opt.value = zone
    list.appendChild(opt)
  }
  host.appendChild(list)
  return list
}

// Repeated-group widget: an array of rows the app assembles itself
// (Menu Board's menu items, World Clock's cities). Each row edits the
// item schema's sub-fields; on any change every row is composed into one
// token via the item's `x-format` and the array of tokens is stored,
// which `launch.template`'s `{?key*}` explodes into repeated params.
//
// Without this the whole property was skipped as unsupported, so an app
// whose entire content lives in an array (Menu Board) installed with
// nothing to show.
function renderArrayField(
  key: string,
  schema: SettingSchema,
  set: SetFn,
  host: HTMLElement,
  seedValue: SettingValue,
): HTMLElement {
  const item = schema.items || {}
  const itemProps = item.properties || {}
  const keys = fieldNames(item)
  const fmt = item['x-format']
  const itemWidget = item['x-widget']
  const required = item.required || []
  const maxItems =
    typeof schema.maxItems === 'number' ? schema.maxItems : Infinity

  // One { field: value } object per row, in display order.
  const rows: Array<Record<string, string>> = []

  const container = document.createElement('div')
  container.className = 'app-cfg-rows'

  const empty = document.createElement('p')
  empty.className = 'app-cfg-empty'
  empty.textContent = 'None yet — use Add to create one.'

  const addBtn = document.createElement('button')
  addBtn.type = 'button'
  addBtn.className = 'app-btn app-btn-light app-cfg-add'
  const addIcon = document.createElement('i')
  addIcon.className = 'ti ti-plus'
  addIcon.setAttribute('aria-hidden', 'true')
  addBtn.append(addIcon, document.createTextNode('Add'))

  const tokenFor = (row: Record<string, string>): string =>
    fmt
      ? applyItemFormat(fmt, row)
      : String(row[keys[0] as string] ?? '').trim()

  const sync = (): void => {
    if (rows.length) empty.remove()
    else container.after(empty)
    addBtn.disabled = rows.length >= maxItems
    const tokens = rows.map(tokenFor).filter(Boolean)
    // No rows (or only blank ones) means the setting is unset, not set
    // to an empty list: `[]` clears none of pruneEmpty's guards in
    // apps.ts, so it would be saved into metadata.app.values as
    // `{ item: [] }` while the launch URL carries no `item=` at all.
    // Undefined keeps the saved values 1:1 with the URL.
    set(key, tokens.length ? tokens : undefined)
  }

  const addRow = (seed: Record<string, string> = {}): HTMLElement | null => {
    if (rows.length >= maxItems) return null
    const row: Record<string, string> = {}
    keys.forEach((k) => {
      row[k] = seed[k] ?? ''
    })
    rows.push(row)

    const rowEl = document.createElement('div')
    rowEl.className = 'app-cfg-row'

    keys.forEach((pk, i) => {
      const sub = itemProps[pk] || {}
      const input = document.createElement('input')
      input.type = 'text'
      input.className = 'app-cfg-input app-cfg-row__field'
      input.value = row[pk] as string
      // The primary field of a timezone item gets the shared IANA
      // datalist, exactly as the standalone timezone widget does.
      if (i === 0 && itemWidget === 'timezone') {
        const list = timezoneList(host)
        if (list) {
          input.setAttribute('list', list.id)
          input.autocomplete = 'off'
        }
        input.placeholder = 'e.g. Europe/London'
        input.setAttribute('aria-label', sub.title || 'Time zone')
      } else {
        input.placeholder = sub.title || (i === 0 ? pk : 'Optional')
        input.setAttribute('aria-label', sub.title || pk)
      }
      input.addEventListener('input', () => {
        row[pk] = input.value
        sync()
      })
      rowEl.appendChild(input)
    })

    const remove = document.createElement('button')
    remove.type = 'button'
    remove.className = 'app-btn app-btn-icon app-cfg-row__remove'
    remove.setAttribute('aria-label', 'Remove')
    const glyph = document.createElement('i')
    glyph.className = 'ti ti-x'
    glyph.setAttribute('aria-hidden', 'true')
    remove.appendChild(glyph)
    remove.addEventListener('click', () => {
      const idx = rows.indexOf(row)
      if (idx >= 0) rows.splice(idx, 1)
      rowEl.remove()
      sync()
    })
    rowEl.appendChild(remove)

    container.appendChild(rowEl)
    return rowEl
  }

  addBtn.addEventListener('click', () => {
    const rowEl = addRow()
    rowEl?.querySelector('input')?.focus()
    sync()
  })

  const control = document.createElement('div')
  control.append(container, addBtn)

  // Edit mode: repopulate from the tokens saved in metadata.app.values.
  const saved = Array.isArray(seedValue) ? seedValue : []
  for (const token of saved) {
    if (typeof token !== 'string' || !token.trim()) continue
    addRow(
      fmt
        ? parseItemToken(fmt, token, required)
        : { [keys[0] as string]: token },
    )
  }

  // Seed the empty hint and the initial value.
  sync()
  return fieldRow(schema, key, control)
}

// Build the control for one property; wire it to `set(key, value)`.
function renderField(
  key: string,
  schema: SettingSchema,
  widget: string,
  set: SetFn,
  host: HTMLElement,
  seedValue: SettingValue,
  cleanups: Array<() => void>,
): HTMLElement | null {
  const id = `app-cfg-${key}`

  if (widget === 'array') {
    return renderArrayField(key, schema, set, host, seedValue)
  }

  // Settings with no generic control — non-location objects. Skip them
  // rather than emit a scalar text input with the wrong value type.
  if (widget === 'unsupported') return null

  if (widget === 'select') {
    const select = document.createElement('select')
    select.className = 'app-cfg-input'
    select.id = id
    const options = schema.enum || []
    // <select> values read back as strings; map the chosen option back
    // to its original enum value so a typed (number/boolean) default
    // still compares equal and isn't emitted into the URL.
    const typed = (raw: string): SettingValue =>
      (options.find((v) => String(v) === raw) as SettingValue) ?? raw
    options.forEach((value) => {
      const opt = document.createElement('option')
      opt.value = String(value)
      // Shared with the asset-name derivation so a name derived from
      // the chosen option matches this rendered label exactly.
      opt.textContent = selectOptionLabel(schema, value as SettingValue)
      if (String(value) === String(schema.default ?? '')) opt.selected = true
      select.appendChild(opt)
    })
    select.addEventListener('change', () => set(key, typed(select.value)))
    return fieldRow(schema, key, select, id)
  }

  if (widget === 'toggle') {
    const wrap = document.createElement('label')
    wrap.className = 'app-cfg-toggle'
    const box = document.createElement('input')
    box.type = 'checkbox'
    box.id = id
    box.checked = schema.default === true
    const text = document.createElement('span')
    text.textContent = schema.title || key
    wrap.append(box, text)
    box.addEventListener('change', () => set(key, box.checked))
    // The toggle carries its own inline label, so don't add a second
    // one.
    const row = document.createElement('div')
    row.className = 'app-cfg-field'
    row.appendChild(wrap)
    if (schema.description) {
      const help = document.createElement('p')
      help.className = 'app-cfg-help'
      help.textContent = schema.description
      row.appendChild(help)
    }
    return row
  }

  if (widget === 'location-map') {
    const mount = document.createElement('div')
    mount.className = 'app-cfg-map'
    // Seed the map (initLocationMap reads data-lat/lng) from the saved
    // value first — so edit mode reopens on the operator's pin — then
    // the schema default, so add mode opens on the app's default rather
    // than the generic centre.
    const seed = (seedValue ?? schema.default) as
      | { lat?: number; lng?: number }
      | undefined
    if (seed && seed.lat !== undefined && seed.lng !== undefined) {
      mount.dataset.lat = String(seed.lat)
      mount.dataset.lng = String(seed.lng)
    }
    const teardown = initLocationMap(mount, {
      onChange: ({ lat, lng }) => set(key, { lat, lng }),
    })
    cleanups.push(teardown)
    return fieldRow(schema, key, mount)
  }

  // Scalar text-like inputs: text, url, number, timezone.
  const input = document.createElement('input')
  input.className = 'app-cfg-input'
  input.id = id
  input.value = schema.default != null ? String(schema.default) : ''
  if (widget === 'number') {
    input.type = 'number'
    if (schema.minimum !== undefined) input.min = String(schema.minimum)
    if (schema.maximum !== undefined) input.max = String(schema.maximum)
  } else if (widget === 'url') {
    input.type = 'url'
  } else if (widget === 'datetime') {
    // A `date-time` string: native picker. Its value is the local
    // wall-clock (`YYYY-MM-DDTHH:mm`), which apps like Timer resolve
    // against the separate time-zone field — matching their docs.
    input.type = 'datetime-local'
  } else if (widget === 'date') {
    input.type = 'date'
  } else if (widget === 'time') {
    input.type = 'time'
  } else {
    input.type = 'text'
  }
  if (widget === 'timezone') {
    const list = timezoneList(host)
    if (list) {
      input.setAttribute('list', list.id)
      input.autocomplete = 'off'
    }
    input.placeholder = 'e.g. Europe/London'
  }
  // Number inputs read back as strings; store a Number so a numeric
  // default compares equal and unchanged numeric defaults don't leak
  // into the URL.
  const read: () => SettingValue =
    widget === 'number'
      ? () => (input.value === '' ? '' : Number(input.value))
      : () => input.value
  input.addEventListener('input', () => set(key, read()))
  return fieldRow(schema, key, input, id)
}

export interface ManifestFormResult {
  // Current values keyed by setting name (seeded with defaults).
  values: Record<string, SettingValue>
  // Schema defaults, needed by buildLaunchUrl to omit unchanged values.
  defaults: Record<string, SettingValue>
}

// Render the whole form for a manifest's settings into `host`,
// starting from `initial` values (falling back to each schema
// `default`). Calls `onChange(values, defaults)` on every edit. Returns
// the live `values`/`defaults` maps (same objects mutated in place).
export function renderManifestForm(
  host: HTMLElement,
  properties: Record<string, SettingSchema>,
  initial: Record<string, SettingValue>,
  onChange: (
    values: Record<string, SettingValue>,
    defaults: Record<string, SettingValue>,
  ) => void,
): ManifestFormResult {
  // Tear down any controls a previous render left on this host (e.g. a
  // Leaflet map + its ResizeObserver) before replacing them, so
  // re-rendering the same host doesn't leak detached maps.
  teardownHost(host)

  const cleanups: Array<() => void> = []
  ;(host as HostWithCleanups).__cfgCleanups = cleanups

  const values: Record<string, SettingValue> = {}
  const defaults: Record<string, SettingValue> = {}
  const set: SetFn = (key, value) => {
    values[key] = value
    onChange(values, defaults)
  }

  let currentGroup: string | null = null
  for (const [key, schema] of Object.entries(properties)) {
    defaults[key] = schema.default as SettingValue
    values[key] =
      key in initial ? initial[key] : (schema.default as SettingValue)

    const group = schema['x-group'] || null
    if (group && group !== currentGroup) {
      const heading = document.createElement('h3')
      heading.className = 'app-cfg-group'
      heading.textContent = group
      host.appendChild(heading)
    }
    currentGroup = group

    const field = renderField(
      key,
      schema,
      widgetFor(schema),
      set,
      host,
      values[key],
      cleanups,
    )
    // Seed a control that was rendered from a saved value (edit mode):
    // renderField reads schema.default for its initial display, so push
    // the saved value back into the control after mounting.
    if (field) {
      seedControl(field, key, values[key])
      host.appendChild(field)
    }
  }

  onChange(values, defaults)
  return { values, defaults }
}

// After a field is built (from schema defaults), overwrite its
// displayed value with the seeded (saved) value so edit mode reopens on
// the operator's last choice. Location maps seed themselves from
// data-lat/lng set before mount, so they're excluded here.
function seedControl(
  field: HTMLElement,
  key: string,
  value: SettingValue,
): void {
  if (value === undefined || value === null) return
  const input = field.querySelector<HTMLInputElement | HTMLSelectElement>(
    `#app-cfg-${key}`,
  )
  if (input) {
    if (input instanceof HTMLInputElement && input.type === 'checkbox') {
      input.checked = value === true
    } else {
      input.value = String(value)
    }
  }
}
