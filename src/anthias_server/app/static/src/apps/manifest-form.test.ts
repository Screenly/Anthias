// DOM tests for the manifest-driven settings form, focused on the
// repeated-group (array) widget. Run with
// `bun test src/anthias_server/app/static/src/apps/manifest-form.test.ts`
// (bunfig.toml preloads a DOM).
//
// Regression: an `array` property used to be skipped as unsupported, so
// the Menu Board app — whose entire content is one array — rendered a
// config form with a title, a currency and no way to enter a single
// menu item. It installed as an app that showed an empty board.

import { beforeEach, describe, expect, test } from 'bun:test'

import { renderManifestForm } from './manifest-form'
import type { SettingSchema, SettingValue } from './types'

// Menu Board's real settings, from
// https://menu-board.srly.io/.well-known/signage-app.json.
const MENU_PROPS: Record<string, SettingSchema> = {
  name: { type: 'string', title: 'Board title', 'x-widget': 'text' },
  currency: {
    type: 'string',
    title: 'Currency symbol',
    default: '',
    'x-widget': 'text',
  },
  item: {
    type: 'array',
    title: 'Menu items',
    description: 'Each line is one item.',
    items: {
      type: 'object',
      'x-format': '{section}|{name}|{price}|{description}',
      properties: {
        section: { type: 'string', title: 'Section' },
        name: { type: 'string', title: 'Item' },
        price: { type: 'string', title: 'Price' },
        description: { type: 'string', title: 'Description' },
      },
      required: ['name'],
    },
  },
  note: { type: 'string', title: 'Footer note', 'x-widget': 'text' },
}

// World Clock's cities, which drive the timezone sub-widget.
const TZ_PROPS: Record<string, SettingSchema> = {
  tz: {
    type: 'array',
    title: 'Cities',
    items: {
      type: 'object',
      'x-widget': 'timezone',
      'x-format': '{zone}|{label}',
      properties: { zone: { type: 'string' }, label: { type: 'string' } },
      required: ['zone'],
    },
  },
}

let host: HTMLElement
let latest: Record<string, SettingValue>

function mount(
  properties: Record<string, SettingSchema>,
  initial: Record<string, SettingValue> = {},
): void {
  renderManifestForm(host, properties, initial, (values) => {
    latest = { ...values }
  })
}

const field = (label: string): HTMLElement => {
  const rows = [...host.querySelectorAll<HTMLElement>('.app-cfg-field')]
  const found = rows.find(
    (r) => r.querySelector('.app-cfg-label')?.textContent === label,
  )
  if (!found) throw new Error(`no field labelled "${label}"`)
  return found
}

const rowsOf = (f: HTMLElement): HTMLElement[] => [
  ...f.querySelectorAll<HTMLElement>('.app-cfg-row'),
]

const inputsOf = (row: HTMLElement): HTMLInputElement[] => [
  ...row.querySelectorAll<HTMLInputElement>('input'),
]

const addButton = (f: HTMLElement): HTMLButtonElement => {
  const btn = f.querySelector<HTMLButtonElement>('.app-cfg-add')
  if (!btn) throw new Error('no Add button')
  return btn
}

function type(input: HTMLInputElement, value: string): void {
  input.value = value
  input.dispatchEvent(new Event('input'))
}

// Fill one row's inputs in order, skipping blanks.
function fillRow(row: HTMLElement, values: string[]): void {
  const inputs = inputsOf(row)
  values.forEach((v, i) => {
    if (v) type(inputs[i] as HTMLInputElement, v)
  })
}

beforeEach(() => {
  host = document.createElement('div')
  document.body.appendChild(host)
  latest = {}
})

describe('array widget — rendering', () => {
  test('an array property renders a field instead of being skipped', () => {
    mount(MENU_PROPS)
    const labels = [
      ...host.querySelectorAll<HTMLElement>('.app-cfg-label'),
    ].map((l) => l.textContent)
    expect(labels).toEqual([
      'Board title',
      'Currency symbol',
      'Menu items',
      'Footer note',
    ])
  })

  test('it starts empty, with an Add button and a hint', () => {
    mount(MENU_PROPS)
    const f = field('Menu items')
    expect(rowsOf(f)).toHaveLength(0)
    expect(f.querySelector('.app-cfg-empty')).not.toBeNull()
    expect(addButton(f).disabled).toBe(false)
    expect(latest.item).toEqual([])
  })

  test('the field description still renders', () => {
    mount(MENU_PROPS)
    const help = field('Menu items').querySelector('.app-cfg-help')
    expect(help?.textContent).toBe('Each line is one item.')
  })

  test('Add creates one input per item sub-field, placeholdered', () => {
    mount(MENU_PROPS)
    const f = field('Menu items')
    addButton(f).click()
    const row = rowsOf(f)[0] as HTMLElement
    expect(inputsOf(row).map((i) => i.placeholder)).toEqual([
      'Section',
      'Item',
      'Price',
      'Description',
    ])
    expect(f.querySelector('.app-cfg-empty')).toBeNull()
  })
})

describe('array widget — editing', () => {
  test('typing composes each row into one token', () => {
    mount(MENU_PROPS)
    const f = field('Menu items')
    addButton(f).click()
    fillRow(rowsOf(f)[0] as HTMLElement, [
      'Coffee',
      'Flat White',
      '3.40',
      'Our house blend',
    ])
    expect(latest.item).toEqual(['Coffee|Flat White|3.40|Our house blend'])
  })

  test('several rows keep their order', () => {
    mount(MENU_PROPS)
    const f = field('Menu items')
    addButton(f).click()
    fillRow(rowsOf(f)[0] as HTMLElement, ['Coffee', 'Espresso', '2.60'])
    addButton(f).click()
    fillRow(rowsOf(f)[1] as HTMLElement, ['Pastries', 'Croissant', '2.80'])
    expect(latest.item).toEqual([
      'Coffee|Espresso|2.60',
      'Pastries|Croissant|2.80',
    ])
  })

  test('a blank optional field drops itself and its separator', () => {
    mount(MENU_PROPS)
    const f = field('Menu items')
    addButton(f).click()
    fillRow(rowsOf(f)[0] as HTMLElement, ['', 'Cortado', '3.10', ''])
    expect(latest.item).toEqual(['Cortado|3.10'])
  })

  test('an untouched row contributes nothing', () => {
    mount(MENU_PROPS)
    const f = field('Menu items')
    addButton(f).click()
    fillRow(rowsOf(f)[0] as HTMLElement, ['Coffee', 'Espresso'])
    addButton(f).click()
    expect(rowsOf(f)).toHaveLength(2)
    expect(latest.item).toEqual(['Coffee|Espresso'])
  })

  test('removing a row drops its token and restores the hint', () => {
    mount(MENU_PROPS)
    const f = field('Menu items')
    addButton(f).click()
    fillRow(rowsOf(f)[0] as HTMLElement, ['Coffee', 'Espresso'])
    addButton(f).click()
    fillRow(rowsOf(f)[1] as HTMLElement, ['Coffee', 'Cortado'])
    expect(latest.item).toHaveLength(2)

    const remove = (rowsOf(f)[0] as HTMLElement).querySelector('button')
    remove?.click()
    expect(rowsOf(f)).toHaveLength(1)
    expect(latest.item).toEqual(['Coffee|Cortado'])

    ;(rowsOf(f)[0] as HTMLElement).querySelector('button')?.click()
    expect(latest.item).toEqual([])
    expect(f.querySelector('.app-cfg-empty')).not.toBeNull()
  })

  test('the array does not disturb its neighbouring scalar fields', () => {
    mount(MENU_PROPS)
    type(
      field('Board title').querySelector('input') as HTMLInputElement,
      'Corner Coffee',
    )
    const f = field('Menu items')
    addButton(f).click()
    fillRow(rowsOf(f)[0] as HTMLElement, ['Coffee', 'Espresso', '2.60'])
    expect(latest.name).toBe('Corner Coffee')
    expect(latest.item).toEqual(['Coffee|Espresso|2.60'])
  })

  test('maxItems caps the rows and disables Add', () => {
    const capped: Record<string, SettingSchema> = {
      item: { ...(MENU_PROPS.item as SettingSchema), maxItems: 1 },
    }
    mount(capped)
    const f = field('Menu items')
    addButton(f).click()
    expect(addButton(f).disabled).toBe(true)
    addButton(f).click()
    expect(rowsOf(f)).toHaveLength(1)
  })
})

describe('array widget — reopening a saved config', () => {
  test('saved tokens come back as populated rows', () => {
    mount(MENU_PROPS, { item: ['Coffee|Espresso|2.60', 'Pastries|Croissant'] })
    const f = field('Menu items')
    const rows = rowsOf(f)
    expect(rows).toHaveLength(2)
    expect(inputsOf(rows[0] as HTMLElement).map((i) => i.value)).toEqual([
      'Coffee',
      'Espresso',
      '2.60',
      '',
    ])
    expect(inputsOf(rows[1] as HTMLElement).map((i) => i.value)).toEqual([
      'Pastries',
      'Croissant',
      '',
      '',
    ])
  })

  test('reopening without editing leaves the tokens byte-identical', () => {
    // The saved tokens are what the launch URL is built from, so a
    // round trip through the form must not rewrite the installed app's
    // URL. Includes the shapes a dropped field makes ambiguous.
    const saved = [
      'Coffee|Flat White|3.40|Our house blend',
      'Coffee|Espresso|2.60',
      'Lunch|Soup|Ask inside',
      'Cortado|3.10',
      'Espresso',
    ]
    mount(MENU_PROPS, { item: saved })
    expect(latest.item).toEqual(saved)
  })

  test('a required field wins the ambiguity: a lone token is the item', () => {
    mount(MENU_PROPS, { item: ['Espresso'] })
    const inputs = inputsOf(rowsOf(field('Menu items'))[0] as HTMLElement)
    expect(inputs.map((i) => i.value)).toEqual(['', 'Espresso', '', ''])
  })

  test('editing a reopened row recomposes it', () => {
    mount(MENU_PROPS, { item: ['Coffee|Espresso|2.60'] })
    const row = rowsOf(field('Menu items'))[0] as HTMLElement
    type(inputsOf(row)[2] as HTMLInputElement, '2.80')
    expect(latest.item).toEqual(['Coffee|Espresso|2.80'])
  })

  test('blank and non-string saved entries are ignored', () => {
    mount(MENU_PROPS, {
      item: ['Coffee|Espresso', '', '   ', null] as SettingValue,
    })
    expect(rowsOf(field('Menu items'))).toHaveLength(1)
    expect(latest.item).toEqual(['Coffee|Espresso'])
  })
})

describe('array widget — timezone items', () => {
  test('the primary sub-field gets the IANA datalist and hint', () => {
    mount(TZ_PROPS)
    const f = field('Cities')
    addButton(f).click()
    const inputs = inputsOf(rowsOf(f)[0] as HTMLElement)
    const zone = inputs[0] as HTMLInputElement
    expect(zone.placeholder).toBe('e.g. Europe/London')
    expect(zone.getAttribute('aria-label')).toBe('Time zone')
    // The datalist is only wired when the runtime can enumerate zones.
    const list = host.querySelector('[data-tz-list]')
    if (list) expect(zone.getAttribute('list')).toBe(list.id)
  })

  test('a city composes zone|label, and zone alone when unlabelled', () => {
    mount(TZ_PROPS)
    const f = field('Cities')
    addButton(f).click()
    fillRow(rowsOf(f)[0] as HTMLElement, ['Europe/Oslo', 'Home'])
    addButton(f).click()
    fillRow(rowsOf(f)[1] as HTMLElement, ['America/New_York', ''])
    expect(latest.tz).toEqual(['Europe/Oslo|Home', 'America/New_York'])
  })
})
