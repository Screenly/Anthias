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
type Outcome = { status: number; body?: string } | { transport: true }

// What each request actually carried, so the chunk tests can assert
// on the wire format the server stages by.
interface SentRequest {
  range: string | null
  uploadId: string | null
  bodyType: string
  bodySize: number
}

const realXhr = globalThis.XMLHttpRequest

let sends: number
let requests: SentRequest[]

// Each send() consumes the next outcome, so a batch can be given a
// different fate per file.
function stubXhr(outcomes: Outcome[]): void {
  let index = 0
  sends = 0
  requests = []
  ;(globalThis as unknown as { XMLHttpRequest: unknown }).XMLHttpRequest =
    function XMLHttpRequestStub() {
      const outcome = outcomes[Math.min(index++, outcomes.length - 1)]
      const handlers: Record<string, (() => void)[]> = {}
      const headers: Record<string, string> = {}
      return {
        status: 'status' in outcome ? outcome.status : 0,
        responseText: ('body' in outcome && outcome.body) || '',
        upload: { addEventListener: () => {} },
        open: () => {},
        setRequestHeader: (name: string, value: string) => {
          headers[name] = value
        },
        getResponseHeader: () => null,
        addEventListener(type: string, fn: () => void) {
          ;(handlers[type] ??= []).push(fn)
        },
        send(fd: FormData) {
          sends += 1
          const body = fd.get('file_upload')
          requests.push({
            range: headers['Content-Range'] ?? null,
            uploadId: headers['X-Upload-Id'] ?? null,
            bodyType: body instanceof Blob ? body.type : '',
            bodySize: body instanceof Blob ? body.size : 0,
          })
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

function fileInputFrom(file: File): HTMLInputElement {
  return {
    value: '',
    files: [file],
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

// A tiny chunk size keeps these fast: chunkSizeFromMeta reads MB, so
// 0.001 gives ~1 KB chunks and a 3 KB file becomes 3 requests.
function setChunkSizeMb(mb: string): void {
  document.head.innerHTML =
    `<meta name="anthias-upload-chunk-mb" content="${mb}">`
}

function bigFile(bytes: number, name = 'big.mp4', type = 'video/mp4'): File {
  return new File([new Uint8Array(bytes)], name, { type })
}

describe('chunked uploads', () => {
  test('a large file is split into sequential ranges covering it', async () => {
    setChunkSizeMb('0.001')
    stubXhr([
      { status: 200, body: '{"upload_id":"abc"}' },
      { status: 200, body: '{"upload_id":"abc"}' },
      { status: 200 },
    ])
    const file = bigFile(2500)
    await window.homeApp().uploadFiles(fileInputFrom(file))

    expect(requests.map((r) => r.range)).toEqual([
      'bytes 0-1047/2500',
      'bytes 1048-2095/2500',
      'bytes 2096-2499/2500',
    ])
  })

  // The client mints the id, so the server's echo cannot move an
  // upload onto a different staged file part-way through.
  test('the same id rides every chunk, whatever the server echoes', async () => {
    setChunkSizeMb('0.001')
    stubXhr([
      { status: 200, body: '{"upload_id":"server-said-this"}' },
      { status: 200, body: '{"upload_id":"and-then-this"}' },
      { status: 200 },
    ])
    await window.homeApp().uploadFiles(fileInputFrom(bigFile(2500)))

    const ids = requests.map((r) => r.uploadId)
    expect(new Set(ids).size).toBe(1)
    expect(ids[0]).not.toBe('server-said-this')
  })

  // The regression this exists for: a raw Blob from File.slice()
  // reports application/octet-stream, and the server uses the
  // browser's type to catch a file whose extension lies about it (a
  // HEIC renamed to .jpg). Losing it means the asset skips
  // normalisation and renders blank on the player.
  test('each chunk keeps the file type, not the slice default', async () => {
    setChunkSizeMb('0.001')
    stubXhr([
      { status: 200, body: '{"upload_id":"abc"}' },
      { status: 200, body: '{"upload_id":"abc"}' },
      { status: 200 },
    ])
    const file = bigFile(2500, 'photo.jpg', 'image/heic')
    await window.homeApp().uploadFiles(fileInputFrom(file))

    expect(requests.map((r) => r.bodyType)).toEqual([
      'image/heic',
      'image/heic',
      'image/heic',
    ])
  })

  test('a file that fits in one chunk sends no range at all', async () => {
    setChunkSizeMb('16')
    stubXhr([{ status: 200 }])
    await window.homeApp().uploadFiles(fileInputFrom(bigFile(2500)))

    expect(sends).toBe(1)
    expect(requests[0].range).toBeNull()
  })

  test('a dropped chunk is resent rather than losing the upload', async () => {
    setChunkSizeMb('0.001')
    stubXhr([
      { status: 200, body: '{"upload_id":"abc"}' },
      { transport: true },
      { status: 200, body: '{"upload_id":"abc"}' },
      { status: 200 },
    ])
    const app = window.homeApp()
    app.mode = 'add'
    await app.uploadFiles(fileInputFrom(bigFile(2500)))

    expect(sends).toBe(4)
    expect(toasts).toEqual([])
    expect(app.mode).toBeNull()
  }, 10000)

  // A 4xx is the server's considered answer: resending wastes the
  // operator's time and, for a proxy size limit, can never succeed.
  test('a rejected chunk is not resent', async () => {
    setChunkSizeMb('0.001')
    stubXhr([
      { status: 200, body: '{"upload_id":"abc"}' },
      { status: 413 },
    ])
    await window.homeApp().uploadFiles(fileInputFrom(bigFile(2500)))

    expect(sends).toBe(2)
    expect(toasts[0]?.message).toContain('File too large')
  })

  // A 200 that is not the chunk acknowledgement is the server
  // refusing this file and answering with its own toast, which the
  // single-shot path would have replayed. Treat it the same way: one
  // file rejected, batch intact.
  test('a non-JSON 200 is a rejection, not a transport failure', async () => {
    setChunkSizeMb('0.001')
    stubXhr([{ status: 200 }])
    await window.homeApp().uploadFiles(fileInputFrom(bigFile(2500)))

    expect(sends).toBe(1)
    // No client-invented error: the server's own toast stands alone.
    expect(toasts).toEqual([])
  })
})

describe('chunked upload failures', () => {
  // The server words these better than any status code can, and the
  // free-space refusal in particular is something the operator can
  // act on directly.
  test("a chunk error shows the server's own explanation", async () => {
    setChunkSizeMb('0.001')
    stubXhr([
      { status: 200, body: '{"upload_id":"abc"}' },
      { status: 507, body: '{"error":"Not enough disk space, free some up"}' },
    ])
    await window.homeApp().uploadFiles(fileInputFrom(bigFile(2500)))

    expect(toasts[0]?.message).toBe('Not enough disk space, free some up')
  })

  // Type validation runs before staging, so a refused file answers the
  // first chunk with the asset table and its own toast. That is one
  // file being rejected, not a transport failure: the rest of the
  // selection must still upload, exactly as single-shot behaves.
  test('a refused file does not kill the rest of the batch', async () => {
    setChunkSizeMb('0.001')
    stubXhr([{ status: 200 }])
    const input = {
      value: '',
      files: [
        new File([new Uint8Array(2500)], 'doc.pdf', { type: 'application/pdf' }),
        new File([new Uint8Array(10)], 'ok.mp4', { type: 'video/mp4' }),
      ],
      form: {
        getAttribute: () => '/assets/upload/',
        querySelector: () => ({ value: 'test-csrf' }),
      },
    } as unknown as HTMLInputElement

    await window.homeApp().uploadFiles(input)

    // Both attempted: the rejection did not abort the batch.
    expect(sends).toBe(2)
  })

  // The commit renames the partial into place before it answers, so a
  // lost reply may mean the asset already exists. Resending would be
  // met with the server's 409, and telling the operator to try again
  // is what produces the duplicate.
  test('a lost response to the final chunk is not resent', async () => {
    setChunkSizeMb('0.001')
    stubXhr([
      { status: 200, body: '{"upload_id":"abc"}' },
      { status: 200, body: '{"upload_id":"abc"}' },
      { transport: true },
    ])
    await window.homeApp().uploadFiles(fileInputFrom(bigFile(2500)))

    expect(sends).toBe(3)
    expect(toasts[0]?.message).toBe(
      'Upload may have finished — check the asset list before ' +
        'uploading it again',
    )
  }, 10000)

  // A staging chunk commits nothing, so resending one is safe — the
  // server seeks and overwrites the same range.
  test('a lost response to a staging chunk still is resent', async () => {
    setChunkSizeMb('0.001')
    stubXhr([
      { status: 200, body: '{"upload_id":"abc"}' },
      { transport: true },
      { status: 200, body: '{"upload_id":"abc"}' },
      { status: 200 },
    ])
    await window.homeApp().uploadFiles(fileInputFrom(bigFile(2500)))

    expect(sends).toBe(4)
    expect(toasts).toEqual([])
  }, 10000)

  // The server mints nothing now: a retry of chunk 0 that lost its
  // response would otherwise strand the staged bytes under an id the
  // client never learned.
  test('the upload id is the client\'s own, sent from the first chunk', async () => {
    setChunkSizeMb('0.001')
    stubXhr([
      { status: 200, body: '{"upload_id":"ignored"}' },
      { status: 200, body: '{"upload_id":"ignored"}' },
      { status: 200 },
    ])
    await window.homeApp().uploadFiles(fileInputFrom(bigFile(2500)))

    const ids = requests.map((r) => r.uploadId)
    expect(ids[0]).toMatch(/^[0-9a-f]{32}$/)
    expect(new Set(ids).size).toBe(1)
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
