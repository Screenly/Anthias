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
  | { status: number; body?: string; trigger?: string; hxRedirect?: string }
  // A response that arrived while the request body was still going
  // out — what a proxy enforcing a body limit does.
  | { status: number; body?: string; trigger?: string; bodyUnfinished: true }
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
        getResponseHeader: (name: string) => {
          if (name === 'HX-Trigger' && 'trigger' in outcome) {
            return outcome.trigger ?? null
          }
          if (name === 'HX-Redirect' && 'hxRedirect' in outcome) {
            return outcome.hxRedirect ?? null
          }
          return null
        },
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
            ('transport' in outcome && outcome.transport === 'cut') ||
            ('bodyUnfinished' in outcome && outcome.bodyUnfinished)
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
// window.location.href is not assignable in happy-dom, and the auth
// path's whole job is to navigate — so record the assignment instead.
let navigations: string[]

beforeEach(() => {
  toasts = []
  refreshes = []
  navigations = []
  Object.defineProperty(window, 'location', {
    configurable: true,
    value: {
      set href(url: string) {
        navigations.push(url)
      },
      get href() {
        return navigations[navigations.length - 1] ?? ''
      },
    },
  })
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

  // Cut while the body was still going out: the server cannot have
  // seen the whole file, so nothing was created and the operator can
  // safely try again.
  test('a dead socket names both causes without asserting one', async () => {
    stubXhr([{ transport: 'cut' }])
    await window.homeApp().uploadFiles(fileInput('big-video.mp4'))

    expect(toasts[0]?.message).toBe(
      'Upload failed mid-transfer — check your connection, or try a ' +
        'smaller file',
    )
  })

  // The single-shot path commits the same way a final chunk does — the
  // server writes the file, creates the row, and answers afterwards —
  // so a reply lost after the body went out is exactly as ambiguous
  // here, and this is the path most uploads take.
  test('a single-shot reply lost after the body went out is unconfirmed', async () => {
    stubXhr([{ transport: true }])
    const app = window.homeApp()
    app.mode = 'add'
    await app.uploadFiles(fileInput('big-video.mp4'))

    expect(toasts[0]?.message).toBe(
      'Upload may have finished — check the asset list before ' +
        'uploading it again',
    )
    expect(app.mode).toBeNull()
  })

  // A proxy enforcing a body limit answers and closes while the
  // browser is still writing, so the response arrives with the body
  // unfinished. Nothing was committed, and saying "may have finished"
  // would send the operator hunting for an asset that cannot exist.
  test('a status answered mid-body is not called ambiguous', async () => {
    stubXhr([{ status: 502, bodyUnfinished: true }])
    await window.homeApp().uploadFiles(fileInput('big-video.mp4'))

    expect(toasts[0]?.message).toBe(
      'The server failed while handling the upload — check the device logs',
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
    // Not "File too large": the file was already split, so the remedy
    // is a smaller chunk, and telling the operator to shrink the file
    // sends them at the one thing that cannot help.
    expect(toasts[0]?.message).toBe(
      'Even split up, each part is too large for a proxy in front of ' +
        'the device — lower the upload chunk size',
    )
  })

  // The single-shot path keeps the original wording: there, the file
  // really is what exceeded the limit.
  test('a 413 on an unsplit upload still blames the file', async () => {
    setChunkSizeMb('16')
    stubXhr([{ status: 413 }])
    await window.homeApp().uploadFiles(fileInputFrom(bigFile(2500)))

    expect(toasts[0]?.message).toContain('File too large')
  })

  // A 200 that is not the chunk acknowledgement is the server
  // refusing this file and answering with its own toast, which the
  // single-shot path would have replayed. Treat it the same way: one
  // file rejected, batch intact.
  // With a toast, the server has explained itself and the file is
  // simply refused — replay it and carry on, as single-shot does.
  test('a non-JSON 200 with a toast is a rejection', async () => {
    setChunkSizeMb('1')
    stubXhr([
      {
        status: 200,
        trigger: '{"toast":{"kind":"error","message":"Invalid file type."}}',
      },
    ])
    await window.homeApp().uploadFiles(fileInputFrom(bigFile(CHUNKED_FILE_BYTES)))

    expect(sends).toBe(1)
    // The server's own toast stands alone — no client-invented error.
    expect(toasts.map((t) => t.message)).toEqual(['Invalid file type.'])
  })

  // Without one, nobody has told the operator anything. A login page, a
  // captive portal or a proxy's own 200 all look like this, and
  // dropping the file in silence leaves no asset, no error, and a
  // progress bar that completed normally.
  test('a non-JSON 200 with no toast is never silent', async () => {
    setChunkSizeMb('1')
    stubXhr([{ status: 200 }])
    await window.homeApp().uploadFiles(fileInputFrom(bigFile(CHUNKED_FILE_BYTES)))

    expect(sends).toBe(1)
    expect(toasts.length).toBe(1)
    expect(toasts[0]?.kind).toBe('error')
  })

  // The expired-session case. An XHR follows a 302 invisibly, so the
  // server answers 2xx + HX-Redirect; the batch must stop and the
  // operator must land on the login page, not lose every file over the
  // chunk size in silence.
  test('an HX-Redirect on a staging chunk aborts and navigates', async () => {
    setChunkSizeMb('1')
    stubXhr([{ status: 200, hxRedirect: '/login/' }])
    const app = window.homeApp()
    await app.uploadFiles(fileInputFrom(bigFile(CHUNKED_FILE_BYTES)))

    expect(sends).toBe(1)
    expect(navigations).toEqual(['/login/'])
    expect(toasts[0]?.message).toBe(
      'Your session expired — sign in again to upload',
    )
  })

  test('an HX-Redirect on the commit aborts and navigates', async () => {
    setChunkSizeMb('1')
    stubXhr([
      { status: 200, body: '{"upload_id":"abc"}' },
      { status: 200, body: '{"upload_id":"abc"}' },
      { status: 200, hxRedirect: '/login/' },
    ])
    await window.homeApp().uploadFiles(fileInputFrom(bigFile(CHUNKED_FILE_BYTES)))

    expect(navigations).toEqual(['/login/'])
    expect(toasts[0]?.message).toBe(
      'Your session expired — sign in again to upload',
    )
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
    const app = window.homeApp()
    app.mode = 'add'
    await app.uploadFiles(fileInputFrom(bigFile(CHUNKED_FILE_BYTES)))

    expect(toasts[0]?.message).toContain('already appears in the asset list')
    // Same instruction as the unconfirmed toast, so the modal has to
    // get out of the way of the list here too.
    expect(app.mode).toBeNull()
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

// --- Page-wide drag and drop -------------------------------------------
//
// The gesture that has to work is "drag a video onto the asset list",
// which is a window-level drop with no dropzone under the pointer. What
// these pin is the routing around it: which drags are claimed from the
// browser (an unclaimed one navigates the tab to the file and the page
// is gone), when the overlay is up, and which drops reach the upload
// path at all. The upload itself is covered above.

type HomeAppLike = ReturnType<typeof window.homeApp>

// happy-dom has no DataTransfer to hang files off, and the handlers
// only ever read `types` and `files`, so a literal is steadier.
type FakeDrag = DragEvent & { prevented: boolean }

function fakeDrag(types: string[], files: File[]): FakeDrag {
  const event = {
    prevented: false,
    dataTransfer: { types, files },
    preventDefault() {
      event.prevented = true
    },
  }
  return event as unknown as FakeDrag
}

function fileDrag(...names: string[]): FakeDrag {
  return fakeDrag(
    ['Files'],
    names.map((n) => new File(['x'], n, { type: 'video/mp4' })),
  )
}

// Dragging selected text, a link, or an image already on the page
// fires the identical events. None of it is an upload.
function textDrag(): FakeDrag {
  return fakeDrag(['text/plain'], [])
}

// What the add-asset modal contributes to the real page: the endpoint
// on the form, the CSRF token, and the input dropFiles() hands the
// FileList to.
function mountUploadForm(): void {
  document.body.innerHTML =
    '<form action="/assets/upload/">' +
    '<input name="csrfmiddlewaretoken" value="test-csrf">' +
    '<input type="file" id="add-file" multiple>' +
    '</form>'
}

describe('page-wide drag and drop', () => {
  afterEach(() => {
    document.body.innerHTML = ''
  })

  test('a file dragged over the page raises the overlay', () => {
    const app = window.homeApp()
    const event = fileDrag('clip.mp4')
    app.onPageDragEnter(event)

    expect(app.pageDragActive).toBe(true)
    // Unclaimed, the browser keeps the drag and navigates to the file
    // on drop — the whole bug this path exists to close.
    expect(event.prevented).toBe(true)
  })

  test('a drag carrying no files is left to the browser', () => {
    const app = window.homeApp()
    const event = textDrag()
    app.onPageDragEnter(event)

    expect(app.pageDragActive).toBe(false)
    expect(event.prevented).toBe(false)
  })

  // dragenter/dragleave fire once per element the pointer crosses, so
  // without the depth counter the overlay would blink off every time
  // the drag moved from one table row to the next.
  test('crossing between elements does not flicker the overlay', () => {
    const app = window.homeApp()
    app.onPageDragEnter(fileDrag('clip.mp4'))
    app.onPageDragEnter(fileDrag('clip.mp4'))
    app.onPageDragLeave(fileDrag('clip.mp4'))

    expect(app.pageDragActive).toBe(true)

    app.onPageDragLeave(fileDrag('clip.mp4'))
    expect(app.pageDragActive).toBe(false)
  })

  // A dragleave with no matching dragenter would push an unfloored
  // counter negative, and every later enter would land on -1 — the
  // overlay would never come up again for the rest of the session.
  test('a stray dragleave cannot strand the counter', () => {
    const app = window.homeApp()
    app.onPageDragLeave(fileDrag('clip.mp4'))
    app.onPageDragEnter(fileDrag('clip.mp4'))

    expect(app.pageDragActive).toBe(true)
  })

  // Every overlay that owns the screen closes through a method that
  // clears the drag state. Without it, a drag in flight when the
  // overlay closed (Escape before the browser delivers the matching
  // dragleave) strands the depth counter: the page overlay and the
  // preview iframe's pointer-events shield stay armed for the rest of
  // the session, and the next Add opens with its dropzone already lit.
  test.each([
    ['closeModal', (app: ReturnType<typeof window.homeApp>) => app.closeModal()],
    ['closePreview', (app: ReturnType<typeof window.homeApp>) => app.closePreview()],
    ['closeBulkEdit', (app: ReturnType<typeof window.homeApp>) => app.closeBulkEdit()],
    ['closeDelete', (app: ReturnType<typeof window.homeApp>) => app.closeDelete()],
    ['closeBulkDelete', (app: ReturnType<typeof window.homeApp>) => app.closeBulkDelete()],
  ])('%s clears a drag left in flight', (_name, close) => {
    const app = window.homeApp()
    app.onPageDragEnter(fileDrag('clip.mp4'))
    app.onPageDragEnter(fileDrag('clip.mp4'))
    expect(app.pageDragActive).toBe(true)

    close(app)

    expect(app.pageDragActive).toBe(false)
    // The counter too: a stale depth would keep the next dragleave
    // from ever reaching zero.
    expect(app.pageDragDepth).toBe(0)
  })

  // The recovery path: a dragenter swallowed by an element that stops
  // propagation still leaves a drag the page can see moving.
  test('dragover raises a highlight that no dragenter did', () => {
    const app = window.homeApp()
    const event = fileDrag('clip.mp4')
    app.onPageDragOver(event)

    expect(app.pageDragActive).toBe(true)
    expect(event.prevented).toBe(true)
  })

  // Re-arming with a zero depth would make the very next enter/leave
  // pair (the pointer crossing a row) hide the overlay again while the
  // file is still over the page.
  test('a recovered highlight survives the next element crossing', () => {
    const app = window.homeApp()
    // The dragenter that never arrived — an element that stopped
    // propagation swallowed it — so the page recovers on dragover.
    app.onPageDragOver(fileDrag('clip.mp4'))
    expect(app.pageDragActive).toBe(true)

    // Pointer crosses into a row and out again.
    app.onPageDragEnter(fileDrag('clip.mp4'))
    app.onPageDragLeave(fileDrag('clip.mp4'))

    expect(app.pageDragActive).toBe(true)
  })

  test('a file dropped on the page uploads it', async () => {
    mountUploadForm()
    stubXhr([{ status: 200 }])
    const app = window.homeApp()
    app.onPageDragEnter(fileDrag('clip.mp4'))
    app.onPageDrop(fileDrag('clip.mp4'))
    // The drop hands off to the batch without awaiting it.
    await Bun.sleep(0)

    expect(sends).toBe(1)
    // Opened on the upload pane, which is where the batch's progress
    // UI lives — otherwise a dropped file uploads with no feedback.
    expect(app.tab).toBe('file')
    expect(app.pageDragActive).toBe(false)
    expect(refreshes).toEqual(['refresh-assets'])
  })

  // Hiding the modal mid-batch leaves uploadState set and mode null,
  // so the page is still listening. dropFiles() refuses to start a
  // second batch over a running one, and a file that vanished with no
  // explanation is the worst outcome — so the drop reopens the modal
  // onto the upload still in flight.
  test('a drop mid-batch reopens the upload in progress', async () => {
    mountUploadForm()
    stubXhr([{ status: 200 }])
    const app = window.homeApp()
    app.uploadState = 'sending'
    app.onPageDrop(fileDrag('second.mp4'))
    await Bun.sleep(0)

    expect(sends).toBe(0)
    expect(app.mode).toBe('add')
    expect(app.tab).toBe('file')
  })

  // Each of these owns the screen with an overlay of its own, so a file
  // released over one is not aimed at the asset list. The drop is still
  // claimed — letting the browser have it would navigate away from the
  // half-finished edit underneath.
  test.each([
    ['an open edit modal', (app: HomeAppLike) => (app.mode = 'edit')],
    [
      'the preview modal',
      (app: HomeAppLike) => (app.previewAsset = {} as never),
    ],
    ['the bulk-edit modal', (app: HomeAppLike) => (app.bulkEditOpen = true)],
    ['the delete prompt', (app: HomeAppLike) => (app.pendingDeleteId = 'a1')],
    [
      'the bulk-delete prompt',
      (app: HomeAppLike) => (app.bulkDeleteOpen = true),
    ],
  ])('a drop over %s is refused, not navigated', async (_name, open) => {
    mountUploadForm()
    stubXhr([{ status: 200 }])
    const app = window.homeApp()
    open(app)
    const event = fileDrag('clip.mp4')
    app.onPageDrop(event)
    await Bun.sleep(0)

    expect(event.prevented).toBe(true)
    expect(sends).toBe(0)
    expect(app.tab).toBe('uri')
    // Refused, but not in silence: every dragover on the page was
    // cancelled on the way in, so the cursor spent the whole drag
    // telling the operator this was a drop target. Dropping into
    // nothing after that reads as a file that evaporated.
    expect(toasts).toEqual([
      {
        kind: 'info',
        message: 'Close this dialog first, then drop your file to upload it.',
        ttlMs: undefined,
      },
    ])
  })

  // openAdd() clears the Add pane on every open, which is right when a
  // drop is what opens it — the last asset's URL should not still be
  // sitting in the box. It is wrong when the modal is already up: the
  // operator is mid-sentence in that box, and a dropped file is not a
  // reason to throw away what they typed. They land on the upload pane
  // either way and can switch back to find it intact.
  test('a drop into an open Add modal keeps what is already typed', async () => {
    mountUploadForm()
    stubXhr([{ status: 200 }])
    const app = window.homeApp()
    app.mode = 'add'
    app.tab = 'uri'
    app.addUri = 'https://example.com/half-typed'
    app.onPageDrop(fileDrag('clip.mp4'))
    await Bun.sleep(0)

    expect(sends).toBe(1)
    expect(app.tab).toBe('file')
    expect(app.addUri).toBe('https://example.com/half-typed')
  })

  // The other half of the same rule: opened BY the drop, the pane is
  // fresh. Guards the `mode !== 'add'` test above from being widened
  // into "never reset".
  test('a drop that opens the Add modal resets the pane', async () => {
    mountUploadForm()
    stubXhr([{ status: 200 }])
    const app = window.homeApp()
    app.addUri = 'https://example.com/left-over'
    app.onPageDrop(fileDrag('clip.mp4'))

    // Read before the batch resolves: a successful upload closes the
    // modal behind itself, so `mode` is only 'add' while it runs.
    expect(app.mode).toBe('add')
    expect(app.addUri).toBe('')
    await Bun.sleep(0)
  })
})
