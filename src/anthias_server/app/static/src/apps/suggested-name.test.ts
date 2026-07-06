// Behavioural tests for the default asset-name derivation. Run with
// `bun test src/anthias_server/app/static/src/apps/suggested-name.test.ts`.
// These pin the regression fix where every store-app install (e.g. the
// RSS Reader) shared the identical manifest name instead of being named
// after its chosen setting.

import { describe, expect, test } from 'bun:test'

import { suggestedName } from './suggested-name'
import type { AppManifest, SettingSchema } from './types'

// A minimal manifest carrying just the fields suggestedName reads,
// plus whatever settings the test supplies.
function manifest(
  name: string,
  properties?: Record<string, SettingSchema>,
): AppManifest {
  return {
    manifestVersion: '1',
    id: 'test.app',
    name,
    description: 'x',
    launch: { baseUrl: 'https://app.srly.io/' },
    ...(properties ? { settings: { type: 'object', properties } } : {}),
  }
}

const FEED: SettingSchema = {
  type: 'string',
  enum: ['https://npr.example/rss', 'https://bbc.example/rss'],
  'x-enumLabels': ['NPR — Top Stories', 'BBC News'],
  default: 'https://npr.example/rss',
}

describe('suggestedName', () => {
  test('uses the default select option label before anything is chosen', () => {
    expect(suggestedName(manifest('RSS Reader', { feed: FEED }), {})).toBe(
      'NPR — Top Stories',
    )
  })

  test('tracks an updated select value', () => {
    expect(
      suggestedName(manifest('RSS Reader', { feed: FEED }), {
        feed: 'https://bbc.example/rss',
      }),
    ).toBe('BBC News')
  })

  test('falls back to the manifest name when no labelled select exists', () => {
    // A plain text setting has no x-enumLabels, so nothing distinguishes
    // installs — keep the manifest name.
    const url: SettingSchema = { type: 'string', 'x-widget': 'url' }
    expect(suggestedName(manifest('Web Page', { url }), {})).toBe('Web Page')
    expect(suggestedName(manifest('Web Page'), {})).toBe('Web Page')
  })

  test('uses the first labelled select in manifest order', () => {
    const region: SettingSchema = {
      type: 'string',
      enum: ['eu', 'us'],
      'x-enumLabels': ['Europe', 'Americas'],
      default: 'eu',
    }
    // FEED is declared first, so its label wins over region's.
    expect(
      suggestedName(manifest('App', { feed: FEED, region }), {}),
    ).toBe('NPR — Top Stories')
  })

  test('mirrors the select renderer for a missing label entry', () => {
    // A select whose x-enumLabels omits the chosen entry: the renderer
    // shows the raw value, so the derived name must too rather than
    // silently reverting to the manifest name.
    const partial: SettingSchema = {
      type: 'string',
      enum: ['alpha', 'beta'],
      'x-enumLabels': ['Alpha'],
      default: 'beta',
    }
    expect(suggestedName(manifest('App', { partial }), {})).toBe('beta')
  })

  test('ignores a select whose value is not one of its options', () => {
    const partial: SettingSchema = {
      type: 'string',
      enum: ['alpha', 'beta'],
      'x-enumLabels': ['Alpha', 'Beta'],
    }
    // No default and a stray value → skip it and keep the manifest name.
    expect(
      suggestedName(manifest('App', { partial }), { partial: 'gamma' }),
    ).toBe('App')
  })
})
