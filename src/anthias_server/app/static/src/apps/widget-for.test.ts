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
    expect(widgetFor({ type: 'array' })).toBe('array')
  })

  test('an array renders as a repeated group, not skipped', () => {
    // Menu Board's items and World Clock's cities: the whole point of
    // the app lives in an array, so returning 'unsupported' here
    // installed the app with nothing to show.
    const menuItems: SettingSchema = {
      type: 'array',
      title: 'Menu items',
      items: {
        type: 'object',
        'x-format': '{section}|{name}|{price}|{description}',
        properties: { section: { type: 'string' }, name: { type: 'string' } },
        required: ['name'],
      },
    }
    expect(widgetFor(menuItems)).toBe('array')
  })

  test('a non-location object is still skipped', () => {
    expect(widgetFor({ type: 'object', properties: { a: {} } })).toBe(
      'unsupported',
    )
  })
})
