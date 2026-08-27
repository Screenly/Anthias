// Behavioural tests for the upload chunk planner. Run with
// `bun test src/anthias_server/app/static/src/home/chunking.test.ts`.
//
// The ranges these produce are what the server stages by, and a wrong
// one fails silently: the server truncates to the declared total, so a
// bad final range or an off-by-one start yields an asset of exactly
// the right size that will not play.

import { describe, expect, test } from 'bun:test'

import { chunkSizeFromMeta, needsChunking, planChunks } from './chunking'

describe('planChunks', () => {
  test('covers every byte exactly once, with no gap or overlap', () => {
    const chunks = planChunks(250, 100)
    expect(chunks.map((c) => [c.start, c.end])).toEqual([
      [0, 99],
      [100, 199],
      [200, 249],
    ])
    // The property that matters: each chunk starts where the last ended.
    chunks.forEach((c, i) => {
      if (i > 0) expect(c.start).toBe(chunks[i - 1].end + 1)
    })
  })

  test('the last chunk ends on the final byte, not past it', () => {
    // The server truncates to the declared total, so an end past the
    // file would silently produce a short asset.
    expect(planChunks(250, 100).at(-1)?.end).toBe(249)
    expect(planChunks(300, 100).at(-1)?.end).toBe(299)
  })

  test('an exact multiple does not emit a trailing empty chunk', () => {
    const chunks = planChunks(300, 100)
    expect(chunks).toHaveLength(3)
    expect(chunks.at(-1)?.end).toBe(299)
  })

  test('the header is the wire format the server parses', () => {
    expect(planChunks(250, 100)[0].header).toBe('bytes 0-99/250')
    expect(planChunks(250, 100).at(-1)?.header).toBe('bytes 200-249/250')
  })

  test('a file smaller than one chunk is a single full-range chunk', () => {
    expect(planChunks(40, 100)).toEqual([
      { start: 0, end: 39, header: 'bytes 0-39/40' },
    ])
  })

  // An empty file has no representable range (0-0/0 would claim one
  // byte), and the server rejects it outright.
  test('an empty file yields no chunks', () => {
    expect(planChunks(0, 100)).toEqual([])
  })
})

describe('needsChunking', () => {
  test('only files larger than one chunk are split', () => {
    expect(needsChunking(101, 100)).toBe(true)
    expect(needsChunking(100, 100)).toBe(false)
    expect(needsChunking(0, 100)).toBe(false)
  })
})

describe('chunkSizeFromMeta', () => {
  test('reads the configured size in MB', () => {
    expect(chunkSizeFromMeta('8')).toBe(8 * 1024 * 1024)
  })

  // The ceiling keeps a chunk under the server's
  // FILE_UPLOAD_MAX_MEMORY_SIZE, where Django holds it in RAM instead
  // of spooling it to the SD card.
  test('caps the size so a chunk stays in memory server-side', () => {
    expect(chunkSizeFromMeta('512')).toBe(24 * 1024 * 1024)
  })

  // The server clamps to the same floor, so this only bites on a meta
  // tag that was hand-edited — but a size below 1 MB turns a large
  // video into thousands of sequential requests, and a small enough
  // one used to floor to 0 bytes, which made planChunks return no
  // chunks for a file needsChunking had just said to split.
  test('floors the size the same way the server does', () => {
    expect(chunkSizeFromMeta('0.5')).toBe(1024 * 1024)
    expect(chunkSizeFromMeta('0.0000001')).toBe(1024 * 1024)
  })

  test('falls back when the tag is missing or nonsense', () => {
    const fallback = 16 * 1024 * 1024
    expect(chunkSizeFromMeta(null)).toBe(fallback)
    expect(chunkSizeFromMeta('')).toBe(fallback)
    expect(chunkSizeFromMeta('nope')).toBe(fallback)
    expect(chunkSizeFromMeta('0')).toBe(fallback)
    expect(chunkSizeFromMeta('-4')).toBe(fallback)
  })
})
