// Unit tests for widgetFor — the manifest-form control picker. Run with
// `bun test src/anthias_server/app/static/src/apps/widget-for.test.ts`.
// Pins the fix where a JSON Schema string `format` (e.g. the Timer app's
// `date-time` target) fell through to a bare text input instead of a
// native date/time picker.

import { describe, expect, test } from 'bun:test'

import type { SettingSchema } from './types'
import { widgetFor } from './widget-for'

describe('widgetFor', () => {
  test('date-time string -> datetime picker', () => {
    expect(widgetFor({ type: 'string', format: 'date-time' })).toBe('datetime')
  })

  test('date / time string formats -> matching pickers', () => {
    expect(widgetFor({ type: 'string', format: 'date' })).toBe('date')
    expect(widgetFor({ type: 'string', format: 'time' })).toBe('time')
  })

  test('unknown / absent format -> plain text', () => {
    expect(widgetFor({ type: 'string' })).toBe('text')
    expect(widgetFor({ type: 'string', format: 'email' })).toBe('text')
  })

  test('an explicit x-widget always wins over format', () => {
    const schema: SettingSchema = {
      type: 'string',
      format: 'date-time',
      'x-widget': 'timezone',
    }
    expect(widgetFor(schema)).toBe('timezone')
  })

  test('non-format widgets are unaffected', () => {
    expect(widgetFor({ type: 'boolean' })).toBe('toggle')
    expect(widgetFor({ type: 'integer' })).toBe('number')
    expect(widgetFor({ type: 'string', enum: ['a', 'b'] })).toBe('select')
    expect(
      widgetFor({ type: 'object', properties: { lat: {}, lng: {} } }),
    ).toBe('location-map')
    expect(widgetFor({ type: 'array' })).toBe('unsupported')
  })
})
