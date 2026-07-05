// Behavioural tests for the manifest launch-URL builder. Run with
// `bun test src/anthias_server/app/static/src/apps/launch-url.test.ts`.
// These pin the RFC 6570 `{?…}` expansion the config form relies on so
// an installed app's URL matches what the app store would build.

import { describe, expect, test } from 'bun:test'

import { buildLaunchUrl } from './launch-url'

describe('buildLaunchUrl', () => {
  test('returns the base URL when there is no template', () => {
    expect(buildLaunchUrl('https://quotes.srly.io/', '')).toBe(
      'https://quotes.srly.io/',
    )
  })

  test('omits values that are empty or still at their default', () => {
    const url = buildLaunchUrl(
      'https://weather.srly.io/',
      '{?locale,24h}',
      { locale: '', '24h': '' },
      { locale: '', '24h': '' },
    )
    expect(url).toBe('https://weather.srly.io/')
  })

  test('emits only the changed values, first one takes the ?', () => {
    const url = buildLaunchUrl(
      'https://weather.srly.io/',
      '{?locale,24h}',
      { locale: '', '24h': '1' },
      { locale: '', '24h': '' },
    )
    expect(url).toBe('https://weather.srly.io/?24h=1')
  })

  test('a boolean true becomes name=1, false is omitted', () => {
    expect(
      buildLaunchUrl(
        'https://world-clock.srly.io/',
        '{?seconds}',
        { seconds: true },
        { seconds: false },
      ),
    ).toBe('https://world-clock.srly.io/?seconds=1')
    expect(
      buildLaunchUrl(
        'https://world-clock.srly.io/',
        '{?seconds}',
        { seconds: false },
        { seconds: false },
      ),
    ).toBe('https://world-clock.srly.io/')
  })

  test('explodes an object into its key=value pairs', () => {
    const url = buildLaunchUrl(
      'https://weather.srly.io/',
      '{?location*}',
      { location: { lat: '51.5', lng: '-0.1' } },
      {},
    )
    expect(url).toBe('https://weather.srly.io/?lat=51.5&lng=-0.1')
  })

  test('explodes an array into repeated params', () => {
    const url = buildLaunchUrl(
      'https://world-clock.srly.io/',
      '{?tz*}',
      { tz: ['Europe/London', 'America/New_York'] },
      {},
    )
    // Slash is kept literal; other reserved chars are percent-encoded.
    expect(url).toBe(
      'https://world-clock.srly.io/?tz=Europe/London&tz=America/New_York',
    )
  })

  test('keeps the zone|label pipe literal in composite tokens', () => {
    const url = buildLaunchUrl(
      'https://world-clock.srly.io/',
      '{?tz*}',
      { tz: ['Europe/Stockholm|Home'] },
      {},
    )
    expect(url).toBe(
      'https://world-clock.srly.io/?tz=Europe/Stockholm|Home',
    )
  })
})
