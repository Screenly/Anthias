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
// Supported `x-widget`s (falling back to the JSON Schema type): text,
// url, number, select (enum), toggle (boolean), timezone, and
// location-map (a {lat,lng} object). Unknown widgets degrade to a text
// input; unsupported structural types (arrays, non-location objects)
// are skipped rather than mis-rendered.

import { initLocationMap } from './location-map'
import { selectOptionLabel } from './select-label'
import type { SettingSchema, SettingValue } from './types'

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

// Which control to render for a settings property.
function widgetFor(schema: SettingSchema): string {
  if (schema['x-widget']) return schema['x-widget']
  if (Array.isArray(schema.enum)) return 'select'
  if (schema.type === 'boolean') return 'toggle'
  if (schema.type === 'number' || schema.type === 'integer') return 'number'
  // Only a {lat,lng} object is a location map; other objects/arrays
  // have no generic control, so mark them unsupported (skipped) rather
  // than mis-render.
  if (schema.type === 'object') {
    const props = schema.properties || {}
    return props.lat && props.lng ? 'location-map' : 'unsupported'
  }
  if (schema.type === 'array') return 'unsupported'
  return 'text'
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

  // Settings with no generic control — arrays and non-location
  // objects. Skip them rather than emit a scalar text input with the
  // wrong value type.
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
