// Unit tests for the array-item token codec. Run with
// `bun test src/anthias_server/app/static/src/apps/item-format.test.ts`.
//
// Pins the fix where an `array` setting was skipped as unsupported, so
// Menu Board installed with no menu items and World Clock with no
// cities: the launch URL carried the scalar fields and not a single
// `item=` / `tz=` param.
//
// The composer is a port of the app store's lib/item-format.js and must
// stay byte-compatible with it — the same rows have to produce the same
// URL whether the operator configures the app in the store or in
// Anthias. The parser is ours alone (the store never reopens a saved
// config) and its contract is the round trip: reopening an installed app
// and saving it untouched must not change its launch URL.

import { describe, expect, test } from 'bun:test'

import {
  applyItemFormat,
  fieldNames,
  parseFormat,
  parseItemToken,
} from './item-format'
import { buildLaunchUrl } from './launch-url'
import type { SettingSchema } from './types'

// The real manifests, from https://signage-apps.com/manifest.json.
const MENU_FMT = '{section}|{name}|{price}|{description}'
const MENU_REQUIRED = ['name']
const MENU_TEMPLATE = '{?name,subtitle,currency,item*,note}'
const MENU_BASE = 'https://menu-board.srly.io/'

const TZ_FMT = '{zone}|{label}'
const TZ_REQUIRED = ['zone']
const TZ_TEMPLATE = '{?title,tz*,locale,format,seconds}'
const TZ_BASE = 'https://world-clock.srly.io/'

describe('parseFormat', () => {
  test('splits fields from their surrounding literals', () => {
    expect(parseFormat(TZ_FMT)).toEqual({
      fields: [
        { sep: '', name: 'zone' },
        { sep: '|', name: 'label' },
      ],
      prefix: '',
      tail: '',
    })
  })

  test('keeps a leading prefix and a trailing literal', () => {
    const parsed = parseFormat('[{a}-{b}]')
    expect(parsed.prefix).toBe('[')
    expect(parsed.tail).toBe(']')
    expect(parsed.fields.map((f) => f.name)).toEqual(['a', 'b'])
  })

  test('a format with no fields yields nothing to compose', () => {
    expect(parseFormat('').fields).toEqual([])
  })
})

describe('fieldNames', () => {
  test('uses the item schema properties, in manifest order', () => {
    const item: SettingSchema = {
      type: 'object',
      'x-format': TZ_FMT,
      properties: { zone: { type: 'string' }, label: { type: 'string' } },
    }
    expect(fieldNames(item)).toEqual(['zone', 'label'])
  })

  test('falls back to the format fields when there are no properties', () => {
    expect(fieldNames({ type: 'object', 'x-format': TZ_FMT })).toEqual([
      'zone',
      'label',
    ])
  })

  test('a bare item schema gets a single unnamed field', () => {
    expect(fieldNames({ type: 'string' })).toEqual(['value'])
  })
})

describe('applyItemFormat', () => {
  test('composes a full row', () => {
    const row = {
      section: 'Coffee',
      name: 'Flat White',
      price: '3.40',
      description: 'Our house blend',
    }
    expect(applyItemFormat(MENU_FMT, row)).toBe(
      'Coffee|Flat White|3.40|Our house blend',
    )
  })

  test('a blank field drops itself and its separator', () => {
    expect(
      applyItemFormat(MENU_FMT, {
        section: 'Coffee',
        name: 'Espresso',
        price: '2.60',
        description: '',
      }),
    ).toBe('Coffee|Espresso|2.60')
    expect(
      applyItemFormat(MENU_FMT, { section: 'Coffee', name: 'Espresso' }),
    ).toBe('Coffee|Espresso')
  })

  test('a blank leading field leaves no leading separator', () => {
    expect(
      applyItemFormat(MENU_FMT, { name: 'Cortado', price: '3.10' }),
    ).toBe('Cortado|3.10')
    expect(applyItemFormat(TZ_FMT, { label: 'Home' })).toBe('Home')
  })

  test('a blank middle field collapses both its separators to one', () => {
    expect(
      applyItemFormat(MENU_FMT, {
        section: 'Lunch',
        name: 'Soup',
        description: 'Ask inside',
      }),
    ).toBe('Lunch|Soup|Ask inside')
  })

  test('values are trimmed', () => {
    expect(applyItemFormat(TZ_FMT, { zone: '  Europe/Oslo ' })).toBe(
      'Europe/Oslo',
    )
  })

  test('an all-blank row composes to nothing', () => {
    expect(applyItemFormat(MENU_FMT, {})).toBe('')
    expect(applyItemFormat(MENU_FMT, { name: '   ' })).toBe('')
  })

  test('prefix and tail survive as long as any field does', () => {
    expect(applyItemFormat('[{a}-{b}]', { a: 'x', b: 'y' })).toBe('[x-y]')
    expect(applyItemFormat('[{a}-{b}]', { b: 'y' })).toBe('[y]')
    expect(applyItemFormat('[{a}-{b}]', {})).toBe('')
  })
})

describe('parseItemToken', () => {
  test('reads a full token field by field', () => {
    expect(
      parseItemToken(
        MENU_FMT,
        'Coffee|Flat White|3.40|Our house blend',
        MENU_REQUIRED,
      ),
    ).toEqual({
      section: 'Coffee',
      name: 'Flat White',
      price: '3.40',
      description: 'Our house blend',
    })
  })

  test('a short token fills the leftmost fields', () => {
    expect(
      parseItemToken(MENU_FMT, 'Coffee|Espresso|2.60', MENU_REQUIRED),
    ).toEqual({
      section: 'Coffee',
      name: 'Espresso',
      price: '2.60',
      description: '',
    })
    expect(
      parseItemToken(MENU_FMT, 'Coffee|Espresso', MENU_REQUIRED),
    ).toEqual({
      section: 'Coffee',
      name: 'Espresso',
      price: '',
      description: '',
    })
  })

  test('a required field is never the one dropped', () => {
    // A lone token is the item name, not the (optional) section — this
    // is the reading the Menu Board app itself applies.
    expect(parseItemToken(MENU_FMT, 'Espresso', MENU_REQUIRED)).toEqual({
      section: '',
      name: 'Espresso',
      price: '',
      description: '',
    })
    expect(parseItemToken(TZ_FMT, 'Europe/Oslo', TZ_REQUIRED)).toEqual({
      zone: 'Europe/Oslo',
      label: '',
    })
  })

  test('a separator inside a value folds into the last field', () => {
    expect(
      parseItemToken(
        MENU_FMT,
        'Lunch|Soup|4.00|Chicken|noodle',
        MENU_REQUIRED,
      ),
    ).toEqual({
      section: 'Lunch',
      name: 'Soup',
      price: '4.00',
      description: 'Chicken|noodle',
    })
  })

  test('an empty token yields an empty row', () => {
    expect(parseItemToken(TZ_FMT, '', TZ_REQUIRED)).toEqual({
      zone: '',
      label: '',
    })
  })

  test('strips a prefix and tail before splitting', () => {
    expect(parseItemToken('[{a}-{b}]', '[x-y]')).toEqual({ a: 'x', b: 'y' })
  })
})

describe('token round trip', () => {
  // Reopening an installed app must not rewrite its launch URL. Every
  // shape the Menu Board app documents, plus the World Clock ones.
  const cases: Array<[string, string, string[]]> = [
    [MENU_FMT, 'Coffee|Flat White|3.40|Our house blend', MENU_REQUIRED],
    [MENU_FMT, 'Coffee|Espresso|2.60', MENU_REQUIRED],
    [MENU_FMT, 'Lunch|Soup|Ask inside', MENU_REQUIRED],
    [MENU_FMT, 'Coffee|Espresso', MENU_REQUIRED],
    [MENU_FMT, 'Cortado|3.10', MENU_REQUIRED],
    [MENU_FMT, 'Espresso', MENU_REQUIRED],
    [MENU_FMT, 'Lunch|Soup|4.00|Chicken|noodle', MENU_REQUIRED],
    [TZ_FMT, 'Europe/Oslo|Home', TZ_REQUIRED],
    [TZ_FMT, 'Europe/Oslo', TZ_REQUIRED],
  ]

  for (const [fmt, token, required] of cases) {
    test(`"${token}" survives parse -> compose`, () => {
      expect(applyItemFormat(fmt, parseItemToken(fmt, token, required))).toBe(
        token,
      )
    })
  }
})

describe('rows -> launch URL', () => {
  const compose = (fmt: string, rows: Array<Record<string, string>>) =>
    rows.map((r) => applyItemFormat(fmt, r)).filter(Boolean)

  test('Menu Board items explode into repeated item= params', () => {
    const item = compose(MENU_FMT, [
      { section: 'Coffee', name: 'Flat White', price: '3.40' },
      { section: 'Coffee', name: 'Espresso', price: '2.60' },
      { section: 'Pastries', name: 'Croissant', price: '2.80' },
    ])
    const url = buildLaunchUrl(MENU_BASE, MENU_TEMPLATE, {
      name: 'Corner Coffee',
      subtitle: '',
      currency: '£',
      item,
      note: '',
    })

    const params = new URL(url).searchParams
    expect(params.get('name')).toBe('Corner Coffee')
    expect(params.get('currency')).toBe('£')
    expect(params.getAll('item')).toEqual([
      'Coffee|Flat White|3.40',
      'Coffee|Espresso|2.60',
      'Pastries|Croissant|2.80',
    ])
    // Empty scalars stay out of the URL entirely.
    expect(params.has('subtitle')).toBe(false)
    expect(params.has('note')).toBe(false)
  })

  test('the encoded form matches what the store emits', () => {
    const url = buildLaunchUrl(MENU_BASE, MENU_TEMPLATE, {
      item: ['Coffee|Flat White|3.40|Our house blend'],
    })
    // `|` and `/` stay literal; everything else is percent-encoded.
    expect(url).toBe(
      `${MENU_BASE}?item=Coffee|Flat%20White|3.40|Our%20house%20blend`,
    )
  })

  test('World Clock cities explode into repeated tz= params', () => {
    const tz = compose(TZ_FMT, [
      { zone: 'Europe/Oslo', label: 'Home' },
      { zone: 'America/New_York', label: '' },
    ])
    const url = buildLaunchUrl(
      TZ_BASE,
      TZ_TEMPLATE,
      { title: 'Offices', tz, locale: '', format: '', seconds: false },
      { locale: '', format: '', seconds: false },
    )
    expect(new URL(url).searchParams.getAll('tz')).toEqual([
      'Europe/Oslo|Home',
      'America/New_York',
    ])
  })

  test('an all-blank row is dropped, not sent as an empty param', () => {
    const item = compose(MENU_FMT, [
      { section: 'Coffee', name: 'Espresso', price: '', description: '' },
      { section: '', name: '', price: '', description: '' },
    ])
    expect(item).toEqual(['Coffee|Espresso'])
    const url = buildLaunchUrl(MENU_BASE, MENU_TEMPLATE, { item })
    expect(new URL(url).searchParams.getAll('item')).toEqual([
      'Coffee|Espresso',
    ])
  })

  test('no rows leaves the app at its base URL', () => {
    expect(buildLaunchUrl(MENU_BASE, MENU_TEMPLATE, { item: [] })).toBe(
      MENU_BASE,
    )
  })
})
