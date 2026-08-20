// Wiring tests for the upload batch. Run with
// `bun test src/anthias_server/app/static/src/home.test.ts`.
//
// home/upload-error.test pins the status → message table; this drives
// the real `uploadFiles` through a stubbed XMLHttpRequest so a
// regression in the plumbing — a status dropped on the way up, a batch
// that fails to abort — cannot pass just because the table still holds.

import { afterEach, beforeEach, describe, expect, test } from 'bun:test'

import './home'

type Toast = { kind: string; message: string; ttlMs?: number }
type Outcome = { status: number } | { transport: true }

const realXhr = globalThis.XMLHttpRequest

let sends: number

// Each send() consumes the next outcome, so a batch can be given a
// different fate per file.
function stubXhr(outcomes: Outcome[]): void {
  let index = 0
  sends = 0
  ;(globalThis as unknown as { XMLHttpRequest: unknown }).XMLHttpRequest =
    function XMLHttpRequestStub() {
      const outcome = outcomes[Math.min(index++, outcomes.length - 1)]
      const handlers: Record<string, (() => void)[]> = {}
      return {
        status: 'status' in outcome ? outcome.status : 0,
        upload: { addEventListener: () => {} },
        open: () => {},
        setRequestHeader: () => {},
        getResponseHeader: () => null,
        addEventListener(type: string, fn: () => void) {
          ;(handlers[type] ??= []).push(fn)
        },
        send() {
          sends += 1
          const type = 'status' in outcome ? 'load' : 'error'
          queueMicrotask(() => handlers[type]?.forEach((fn) => fn()))
        },
      }
    }
}

// `uploadFiles` only reaches for `files`, `form` and `value`, so a
// literal is steadier here than building a real FileList in happy-dom.
function fileInput(...names: string[]): HTMLInputElement {
  return {
    value: '',
    files: names.map((n) => new File(['x'], n, { type: 'video/mp4' })),
    form: {
      getAttribute: () => '/assets/upload/',
      querySelector: () => ({ value: 'test-csrf' }),
    },
  } as unknown as HTMLInputElement
}

let toasts: Toast[]
let refreshes: string[]

beforeEach(() => {
  toasts = []
  refreshes = []
  ;(window as unknown as { Alpine: unknown }).Alpine = {
    store: () => ({
      push: (kind: string, message: string, ttlMs?: number) =>
        toasts.push({ kind, message, ttlMs }),
    }),
  }
  ;(window as unknown as { htmx: unknown }).htmx = {
    trigger: (_target: string, event: string) => refreshes.push(event),
  }
})

// bun runs every test file in one process, so leaving these replaced
// would hand the stubs to any later file that touches them.
afterEach(() => {
  globalThis.XMLHttpRequest = realXhr
  delete (window as unknown as { Alpine?: unknown }).Alpine
  delete (window as unknown as { htmx?: unknown }).htmx
})

describe('uploadFiles error reporting', () => {
  test('a proxy 413 surfaces the size-limit message', async () => {
    stubXhr([{ status: 413 }])
    await window.homeApp().uploadFiles(fileInput('big-video.mp4'))

    expect(toasts[0]?.kind).toBe('error')
    expect(toasts[0]?.message).toBe(
      'File too large — it exceeds the upload size limit of the server ' +
        'or a proxy in front of it',
    )
  })

  test('a dead socket names both causes without asserting one', async () => {
    stubXhr([{ transport: true }])
    await window.homeApp().uploadFiles(fileInput('big-video.mp4'))

    expect(toasts[0]?.message).toBe(
      'Upload failed mid-transfer — check your connection, or try a ' +
        'smaller file',
    )
  })

  test('an unremarkable 400 keeps the original wording', async () => {
    stubXhr([{ status: 400 }])
    await window.homeApp().uploadFiles(fileInput('bad.mp4'))

    expect(toasts[0]?.message).toBe(
      'Upload failed — check the file and try again',
    )
  })

  // These are the longest strings the store carries, so they outlast
  // the 4s default a shorter toast gets.
  test('an upload error stays on screen longer than the default', async () => {
    stubXhr([{ status: 413 }])
    await window.homeApp().uploadFiles(fileInput('big-video.mp4'))

    expect(toasts[0]?.ttlMs).toBe(8000)
  })
})

describe('uploadFiles batch behaviour', () => {
  test('a successful upload closes the modal and refreshes the table', async () => {
    stubXhr([{ status: 200 }])
    const app = window.homeApp()
    app.mode = 'add'
    await app.uploadFiles(fileInput('fine.mp4'))

    expect(toasts).toEqual([])
    expect(app.mode).toBeNull()
    expect(refreshes).toEqual(['refresh-assets'])
  })

  // Whatever went wrong applies to the rest of the selection too, so
  // the batch stops rather than hammering on — and reports once.
  test('a transport failure aborts the rest of the batch', async () => {
    stubXhr([{ transport: true }])
    await window.homeApp().uploadFiles(fileInput('a.mp4', 'b.mp4', 'c.mp4'))

    expect(toasts).toHaveLength(1)
    // The real assertion: files b and c were never even attempted.
    expect(sends).toBe(1)
  })

  // A batch that fails partway still lands the rows that made it, so
  // the operator does not re-upload files that are already stored.
  test('a partial batch commits its successes and still reports', async () => {
    stubXhr([{ status: 200 }, { transport: true }])
    const app = window.homeApp()
    app.mode = 'add'
    await app.uploadFiles(fileInput('good.mp4', 'doomed.mp4'))

    expect(refreshes).toEqual(['refresh-assets'])
    expect(app.mode).toBeNull()
    expect(toasts).toHaveLength(1)
  })
})
