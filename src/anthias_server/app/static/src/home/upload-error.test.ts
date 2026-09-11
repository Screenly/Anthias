// Behavioural tests for the upload failure → toast message mapping.
// Run with
// `bun test src/anthias_server/app/static/src/home/upload-error.test.ts`.
//
// These pin the status the UI previously swallowed — 413, which a
// proxy in front of Anthias returns when the body exceeds its limit
// (Cloudflare's 100 MB cap on Free and Pro being the common one).

import { describe, expect, test } from 'bun:test'

import { uploadErrorMessage } from './upload-error'

describe('uploadErrorMessage', () => {
  test('413 names the size limit rather than blaming the file', () => {
    expect(uploadErrorMessage({ kind: 'http', status: 413 })).toBe(
      'File too large — it exceeds the upload size limit of the server ' +
        'or a proxy in front of it',
    )
  })

  test('403 points at the stale page rather than the file', () => {
    expect(uploadErrorMessage({ kind: 'http', status: 403 })).toBe(
      'Upload rejected — reload the page and try again',
    )
  })

  test('5xx points at the device logs', () => {
    const expected =
      'The server failed while handling the upload — check the device logs'
    expect(uploadErrorMessage({ kind: 'http', status: 500 })).toBe(expected)
    expect(uploadErrorMessage({ kind: 'http', status: 502 })).toBe(expected)
  })

  // A chunked upload answers a full disk with 507, so this is now
  // reachable from the browser and must not send the operator to the
  // logs for something they can fix themselves.
  test('507 names the disk, not the logs', () => {
    expect(uploadErrorMessage({ kind: 'http', status: 507 })).toBe(
      'Not enough space on the device — free some up and try again',
    )
  })

  // Never "try again": the asset may already be there, and a second
  // upload is exactly the duplicate this wording exists to avoid.
  test('an unconfirmed commit sends the operator to the list', () => {
    expect(uploadErrorMessage({ kind: 'unconfirmed' })).toBe(
      'Upload may have finished — check the asset list before ' +
        'uploading it again',
    )
  })

  // The server's own wording beats anything derived from a status.
  test('a server-supplied message is passed through verbatim', () => {
    expect(
      uploadErrorMessage({ kind: 'server', message: 'Disk is full.' }),
    ).toBe('Disk is full.')
  })

  test('other 4xx keep the original generic wording', () => {
    const expected = 'Upload failed — check the file and try again'
    expect(uploadErrorMessage({ kind: 'http', status: 400 })).toBe(expected)
    expect(uploadErrorMessage({ kind: 'http', status: 404 })).toBe(expected)
    expect(uploadErrorMessage({ kind: 'http', status: 415 })).toBe(expected)
  })

  // A lost connection and a proxy rejecting an oversized body are
  // indistinguishable here — the browser does not always salvage the
  // 413 — so the message names both and asserts neither.
  test('a transport failure names both causes', () => {
    expect(uploadErrorMessage({ kind: 'network' })).toBe(
      'Upload failed mid-transfer — check your connection, or try a ' +
        'smaller file',
    )
  })

  // A stray status 0 should never reach here (the caller maps it to
  // `network`), but pin the fallback so a leak can't render an empty
  // or nonsensical toast.
  test('a stray status 0 still yields the generic message', () => {
    expect(uploadErrorMessage({ kind: 'http', status: 0 })).toBe(
      'Upload failed — check the file and try again',
    )
  })
})
