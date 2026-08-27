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
type ProgressLike = {
  lengthComputable: boolean
  loaded: number
  total: number
}
// `transport: true` is a response that never came back after the
// request body had gone out — the ambiguous case, since the server may
// have acted on it. `transport: 'cut'` is the connection dying while
// the body was still going out, which the server cannot have seen in
// full.
type Outcome =
  | { status: number; body?: string; trigger?: string }
  | { transport: true }
  | { transport: 'cut' }

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
      const uploadHandlers: Record<
        string,
        ((ev: ProgressLike) => void)[]
      > = {}
      const headers: Record<string, string> = {}
      return {
        status: 'status' in outcome ? outcome.status : 0,
        responseText: ('body' in outcome && outcome.body) || '',
        upload: {
          addEventListener(type: string, fn: (ev: ProgressLike) => void) {
            ;(uploadHandlers[type] ??= []).push(fn)
          },
        },
        open: () => {},
        setRequestHeader: (name: string, value: string) => {
          headers[name] = value
        },
        getResponseHeader: (name: string) =>
          name === 'HX-Trigger' && 'trigger' in outcome
            ? (outcome.trigger ?? null)
            : null,
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
          // A real XHR streams the body out first: progress events,
          // then upload.load once it is all gone, and only then the
          // response (or the error). Modelled here because uploadOne
          // reads the difference — a body that never finished cannot
          // have committed anything.
          const bodySize = requests[requests.length - 1].bodySize
          const cutMidBody =
            'transport' in outcome && outcome.transport === 'cut'
          const type = 'status' in outcome ? 'load' : 'error'
          queueMicrotask(() => {
            uploadHandlers['progress']?.forEach((fn) =>
              fn({
                lengthComputable: true,
                loaded: cutMidBody ? Math.floor(bodySize / 2) : bodySize,
                total: bodySize,
              }),
            )
            if (!cutMidBody) uploadHandlers['load']?.forEach((fn) => fn())
            handlers[type]?.forEach((fn) => fn())
          })
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
  document
    .querySelectorAll('meta[name="anthias-upload-chunk-mb"]')
    .forEach((m) => m.remove())
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
// Appended rather than assigned over document.head: replacing it wipes
// the anthias-date-format / anthias-use-24h metas home.ts reads, and —
// since bun runs every file in one process — leaks the chunk size into
// tests that never asked for it, silently rerouting them through the
// chunked path. Cleared in afterEach.
function setChunkSizeMb(mb: string): void {
  const meta = document.createElement('meta')
  meta.setAttribute('name', 'anthias-upload-chunk-mb')
  meta.setAttribute('content', mb)
  document.head.appendChild(meta)
}

function bigFile(bytes: number, name = 'big.mp4', type = 'video/mp4'): File {
  return new File([new Uint8Array(bytes)], name, { type })
}

// 1 MB chunks is the smallest the server will ever hand the browser
// (resolve_upload_chunk_size_mb clamps to [1, 24]), so drive these at
// that rather than at a size no device can produce. 2.5 MB gives three
// chunks with a short tail.
const CHUNK_MB = 1
const CHUNKED_FILE_BYTES = 2.5 * 1024 * 1024

describe('chunked uploads', () => {
  test('a large file is split into sequential ranges covering it', async () => {
    setChunkSizeMb('1')
    stubXhr([
      { status: 200, body: '{"upload_id":"abc"}' },
      { status: 200, body: '{"upload_id":"abc"}' },
      { status: 200 },
    ])
    const file = bigFile(CHUNKED_FILE_BYTES)
    await window.homeApp().uploadFiles(fileInputFrom(file))

    // Asserted as properties rather than three literal ranges: the
    // exact boundaries follow from the chunk size, and pinning them
    // would make this fail for a change that is still correct. What
    // must hold is that the ranges are contiguous, start at 0, end at
    // the last byte, and all declare the same total — a gap reads back
    // as zeros and an off-by-one truncates, both silently.
    const parsed = requests.map((r) => {
      const m = /^bytes (\d+)-(\d+)\/(\d+)$/.exec(r.range ?? '')
      if (m === null) throw new Error(`unparseable range: ${r.range}`)
      return { start: +m[1], end: +m[2], total: +m[3] }
    })
    expect(parsed.length).toBeGreaterThan(1)
    expect(parsed[0].start).toBe(0)
    expect(parsed[parsed.length - 1].end).toBe(file.size - 1)
    for (const p of parsed) expect(p.total).toBe(file.size)
    for (let i = 1; i < parsed.length; i++) {
      expect(parsed[i].start).toBe(parsed[i - 1].end + 1)
    }
  })

  // The client mints the id, so the server's echo cannot move an
  // upload onto a different staged file part-way through.
  test('the same id rides every chunk, whatever the server echoes', async () => {
    setChunkSizeMb('1')
    stubXhr([
      { status: 200, body: '{"upload_id":"server-said-this"}' },
      { status: 200, body: '{"upload_id":"and-then-this"}' },
      { status: 200 },
    ])
    await window.homeApp().uploadFiles(fileInputFrom(bigFile(CHUNKED_FILE_BYTES)))

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
    setChunkSizeMb('1')
    stubXhr([
      { status: 200, body: '{"upload_id":"abc"}' },
      { status: 200, body: '{"upload_id":"abc"}' },
      { status: 200 },
    ])
    const file = bigFile(CHUNKED_FILE_BYTES, 'photo.jpg', 'image/heic')
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
    await window.homeApp().uploadFiles(fileInputFrom(bigFile(CHUNKED_FILE_BYTES)))

    expect(sends).toBe(1)
    expect(requests[0].range).toBeNull()
  })

  test('a dropped chunk is resent rather than losing the upload', async () => {
    setChunkSizeMb('1')
    stubXhr([
      { status: 200, body: '{"upload_id":"abc"}' },
      { transport: true },
      { status: 200, body: '{"upload_id":"abc"}' },
      { status: 200 },
    ])
    const app = window.homeApp()
    app.mode = 'add'
    await app.uploadFiles(fileInputFrom(bigFile(CHUNKED_FILE_BYTES)))

    expect(sends).toBe(4)
    expect(toasts).toEqual([])
    expect(app.mode).toBeNull()
  }, 10000)

  // A 4xx is the server's considered answer: resending wastes the
  // operator's time and, for a proxy size limit, can never succeed.
  test('a rejected chunk is not resent', async () => {
    setChunkSizeMb('1')
    stubXhr([
      { status: 200, body: '{"upload_id":"abc"}' },
      { status: 413 },
    ])
    await window.homeApp().uploadFiles(fileInputFrom(bigFile(CHUNKED_FILE_BYTES)))

    expect(sends).toBe(2)
    expect(toasts[0]?.message).toContain('File too large')
  })

  // A 200 that is not the chunk acknowledgement is the server
  // refusing this file and answering with its own toast, which the
  // single-shot path would have replayed. Treat it the same way: one
  // file rejected, batch intact.
  test('a non-JSON 200 is a rejection, not a transport failure', async () => {
    setChunkSizeMb('1')
    stubXhr([{ status: 200 }])
    await window.homeApp().uploadFiles(fileInputFrom(bigFile(CHUNKED_FILE_BYTES)))

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
    setChunkSizeMb('1')
    stubXhr([
      { status: 200, body: '{"upload_id":"abc"}' },
      { status: 507, body: '{"error":"Not enough disk space, free some up"}' },
    ])
    await window.homeApp().uploadFiles(fileInputFrom(bigFile(CHUNKED_FILE_BYTES)))

    expect(toasts[0]?.message).toBe('Not enough disk space, free some up')
  })

  // Type validation runs before staging, so a refused file answers the
  // first chunk with the asset table and its own toast. That is one
  // file being rejected, not a transport failure: the rest of the
  // selection must still upload, exactly as single-shot behaves.
  test('a refused file does not kill the rest of the batch', async () => {
    setChunkSizeMb('1')
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
    setChunkSizeMb('1')
    stubXhr([
      { status: 200, body: '{"upload_id":"abc"}' },
      { status: 200, body: '{"upload_id":"abc"}' },
      { transport: true },
    ])
    await window.homeApp().uploadFiles(fileInputFrom(bigFile(CHUNKED_FILE_BYTES)))

    expect(sends).toBe(3)
    expect(toasts[0]?.message).toBe(
      'Upload may have finished — check the asset list before ' +
        'uploading it again',
    )
  }, 10000)

  // Behind a proxy the lost-commit case usually arrives as a gateway
  // 502/504, not a socket error. Exempting only `status === 0` from
  // the retry would leave the common shape of it being resent, into
  // the server's 409, for an upload that had already worked.
  test('a 5xx answer to the final chunk is not resent either', async () => {
    setChunkSizeMb('1')
    stubXhr([
      { status: 200, body: '{"upload_id":"abc"}' },
      { status: 200, body: '{"upload_id":"abc"}' },
      { status: 502 },
    ])
    await window.homeApp().uploadFiles(fileInputFrom(bigFile(CHUNKED_FILE_BYTES)))

    expect(sends).toBe(3)
    expect(toasts[0]?.message).toBe(
      'Upload may have finished — check the asset list before ' +
        'uploading it again',
    )
  }, 10000)

  // The 409 exists to say the asset may already be there. It is
  // reachable almost only on the final chunk, so a commit that reports
  // only its status code throws away the one message that matters.
  test("the final chunk shows the server's own explanation", async () => {
    setChunkSizeMb('1')
    stubXhr([
      { status: 200, body: '{"upload_id":"abc"}' },
      { status: 200, body: '{"upload_id":"abc"}' },
      {
        status: 409,
        body: '{"error":"This upload could not be resumed. Check whether it already appears in the asset list before uploading it again."}',
      },
    ])
    await window.homeApp().uploadFiles(fileInputFrom(bigFile(CHUNKED_FILE_BYTES)))

    expect(toasts[0]?.message).toContain('already appears in the asset list')
  })

  // A body that never finished going out cannot have committed
  // anything, so this one is safe to resend and must not be reported
  // as "may have finished".
  test('a connection cut mid-commit is resent, not called ambiguous', async () => {
    setChunkSizeMb('1')
    stubXhr([
      { status: 200, body: '{"upload_id":"abc"}' },
      { status: 200, body: '{"upload_id":"abc"}' },
      { transport: 'cut' },
    ])
    await window.homeApp().uploadFiles(fileInputFrom(bigFile(CHUNKED_FILE_BYTES)))

    expect(sends).toBe(5)
    expect(toasts[0]?.message).toBe(
      'Upload failed mid-transfer — check your connection, or try a ' +
        'smaller file',
    )
  }, 10000)

  // The toast says to check the asset list; the Add modal covers it.
  test('an unconfirmed commit closes the modal', async () => {
    setChunkSizeMb('1')
    stubXhr([
      { status: 200, body: '{"upload_id":"abc"}' },
      { status: 200, body: '{"upload_id":"abc"}' },
      { transport: true },
    ])
    const app = window.homeApp()
    app.mode = 'add'
    await app.uploadFiles(fileInputFrom(bigFile(CHUNKED_FILE_BYTES)))

    expect(app.mode).toBeNull()
  }, 10000)

  // The server refuses some files with 200 plus an error toast rather
  // than a status code (invalid type, nothing uploaded). Treating that
  // as success would close the modal, count it as uploaded and fire a
  // table refresh for an asset that does not exist. No test supplied
  // an HX-Trigger at all before, so the whole classification was
  // unpinned.
  test('a 200 carrying an error toast is a refusal, not a success', async () => {
    setChunkSizeMb('16')
    stubXhr([
      {
        status: 200,
        trigger: '{"toast":{"kind":"error","message":"Invalid file type."}}',
      },
    ])
    const app = window.homeApp()
    app.mode = 'add'
    await app.uploadFiles(fileInputFrom(bigFile(2500)))

    expect(toasts[0]?.message).toBe('Invalid file type.')
    // Not counted as a success: modal stays open, no table refresh.
    expect(app.mode).toBe('add')
    expect(refreshes).toEqual([])
  })

  test('a 200 carrying a success toast is a success', async () => {
    setChunkSizeMb('16')
    stubXhr([
      {
        status: 200,
        trigger: '{"toast":{"kind":"success","message":"Uploaded."}}',
      },
    ])
    const app = window.homeApp()
    app.mode = 'add'
    await app.uploadFiles(fileInputFrom(bigFile(2500)))

    expect(app.mode).toBeNull()
    expect(refreshes).toEqual(['refresh-assets'])
  })

  // A staging chunk commits nothing, so resending one is safe — the
  // server seeks and overwrites the same range.
  test('a lost response to a staging chunk still is resent', async () => {
    setChunkSizeMb('1')
    stubXhr([
      { status: 200, body: '{"upload_id":"abc"}' },
      { transport: true },
      { status: 200, body: '{"upload_id":"abc"}' },
      { status: 200 },
    ])
    await window.homeApp().uploadFiles(fileInputFrom(bigFile(CHUNKED_FILE_BYTES)))

    expect(sends).toBe(4)
    expect(toasts).toEqual([])
  }, 10000)

  // The client no longer takes the server's id — the server still
  // mints one when a chunk arrives without the header, for any other
  // caller. A retry of chunk 0 that lost its response would otherwise
  // come back with no id, get a second one, and strand the bytes
  // already staged under an id the client never learned.
  test('the upload id is the client\'s own, sent from the first chunk', async () => {
    setChunkSizeMb('1')
    stubXhr([
      { status: 200, body: '{"upload_id":"ignored"}' },
      { status: 200, body: '{"upload_id":"ignored"}' },
      { status: 200 },
    ])
    await window.homeApp().uploadFiles(fileInputFrom(bigFile(CHUNKED_FILE_BYTES)))

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
