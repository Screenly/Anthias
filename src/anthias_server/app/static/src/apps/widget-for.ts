// Pure control-picker for the manifest-driven settings form: maps a
// setting's JSON Schema to the widget key manifest-form.ts renders. Kept
// in its own module (no leaflet / DOM imports) so the mapping is unit-
// testable without dragging in the browser-only location-map chain.

import type { SettingSchema } from './types'

// Standard JSON Schema string `format`s we render as a native typed
// input. A `date-time` becomes a real date/time picker instead of a
// bare text box the operator has to hand-type an ISO string into.
const FORMAT_WIDGET: Record<string, string> = {
  'date-time': 'datetime',
  date: 'date',
  time: 'time',
}

// Which control to render for a settings property.
export function widgetFor(schema: SettingSchema): string {
  if (schema['x-widget']) return schema['x-widget']
  if (Array.isArray(schema.enum)) return 'select'
  if (schema.type === 'boolean') return 'toggle'
  if (schema.type === 'number' || schema.type === 'integer') return 'number'
  // Only a {lat,lng} object is a location map; other objects have no
  // generic control, so mark them unsupported (skipped) rather than
  // mis-render.
  if (schema.type === 'object') {
    const props = schema.properties || {}
    return props.lat && props.lng ? 'location-map' : 'unsupported'
  }
  // A repeated group of rows, each composed into one token by the item
  // schema's `x-format` (Menu Board's items, World Clock's cities).
  if (schema.type === 'array') return 'array'
  // A string with a date/time `format` gets the matching native picker.
  if (schema.type === 'string' && schema.format) {
    const w = FORMAT_WIDGET[schema.format]
    if (w) return w
  }
  return 'text'
}
