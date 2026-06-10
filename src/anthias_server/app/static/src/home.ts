// Home page (Schedule Overview) client logic. Loaded by home.html as
// a deferred bundle alongside vendor.js, so window.flatpickr is
// guaranteed to exist by the time `requestAnimationFrame` fires the
// modal binding callback.

import type Alpine from 'alpinejs'
import type flatpickrLib from 'flatpickr'

declare global {
  interface Window {
    Alpine: typeof Alpine
    flatpickr: typeof flatpickrLib
    homeApp: () => HomeAppData
    fallbackCopyToClipboard: (text: string) => boolean
  }
}

interface AssetEdit {
  asset_id: string
  name: string | null
  uri: string | null
  mimetype: string | null
  duration: number
  is_enabled: boolean
  nocache: boolean
  skip_asset_check: boolean
  start_date_local: string
  end_date_local: string
  play_days_list: number[]
  play_time_from: string | null
  play_time_to: string | null
}

type UploadState = null | 'sending' | 'processing'

interface ToastStoreLike {
  push(kind: 'success' | 'error' | 'info', message: string): number
}

type SectionKey = 'active' | 'inactive'

interface HomeAppData {
  mode: 'add' | 'edit' | null
  editAsset: AssetEdit | null
  previewAsset: AssetEdit | null
  pendingDeleteId: string | null
  pendingDeleteName: string
  uploadState: UploadState
  uploadProgress: number
  uploadFileName: string
  uploadIndex: number
  uploadTotal: number
  // Bulk selection / actions (#3046)
  selectedIds: string[]
  visibleIds: Record<SectionKey, string[]>
  bulkEditOpen: boolean
  bulkDeleteOpen: boolean
  init(): void
  openAdd(): void
  openEdit(asset: AssetEdit): void
  openPreview(asset: AssetEdit): void
  openDelete(id: string, name: string): void
  closeModal(): void
  closePreview(): void
  bindFlatpickr(): void
  uploadFiles(input: HTMLInputElement): Promise<void>
  uploadOne(url: string, csrf: string, file: File): Promise<UploadResult>
  // Bulk selection helpers
  isSelected(id: string): boolean
  toggleSelect(id: string): void
  syncVisibleIds(activeIds: string[], inactiveIds: string[]): void
  sectionAllSelected(section: SectionKey): boolean
  sectionSomeSelected(section: SectionKey): boolean
  toggleSection(section: SectionKey, checked: boolean): void
  clearSelection(): void
  openBulkEdit(): void
  closeBulkEdit(): void
}

// 'ok'       — file accepted and the asset row was created.
// 'rejected' — server reached, but it refused this file (HTTP 200 +
//              error toast, e.g. invalid type). The toast already
//              informed the user; the batch skips it and carries on.
// 'error'    — transport failure / non-2xx. Aborts the batch.
type UploadResult = 'ok' | 'rejected' | 'error'

const DATE_FMT_MAP: Record<string, string> = {
  'mm/dd/yyyy': 'm/d/Y',
  'dd/mm/yyyy': 'd/m/Y',
  'yyyy/mm/dd': 'Y/m/d',
  'mm-dd-yyyy': 'm-d-Y',
  'dd-mm-yyyy': 'd-m-Y',
  'yyyy-mm-dd': 'Y-m-d',
  'mm.dd.yyyy': 'm.d.Y',
  'dd.mm.yyyy': 'd.m.Y',
  'yyyy.mm.dd': 'Y.m.d',
}

function metaContent(name: string): string {
  const el = document.querySelector<HTMLMetaElement>(
    `meta[name="${name}"]`,
  )
  return el?.content ?? ''
}

function csrfToken(): string {
  const fromForm = document.querySelector<HTMLInputElement>(
    'input[name=csrfmiddlewaretoken]',
  )
  if (fromForm) return fromForm.value
  const cookieMatch = document.cookie.match(/(?:^|;\s*)csrftoken=([^;]+)/)
  return cookieMatch ? decodeURIComponent(cookieMatch[1]) : ''
}

function homeApp(): HomeAppData {
  return {
    mode: null,
    editAsset: null,
    previewAsset: null,
    pendingDeleteId: null,
    pendingDeleteName: '',
    uploadState: null,
    uploadProgress: 0,
    uploadFileName: '',
    uploadIndex: 0,
    uploadTotal: 0,
    selectedIds: [],
    visibleIds: { active: [], inactive: [] },
    bulkEditOpen: false,
    bulkDeleteOpen: false,

    init(this: HomeAppData & { $watch: (k: string, cb: () => void) => void }) {
      // Re-bind Flatpickr every time the edit modal opens. The
      // <template x-if> doesn't mount its children until the
      // expression is true, so defer to the next animation frame
      // (after Alpine has actually inserted the form into the DOM)
      // before querying for inputs.
      this.$watch('editAsset', () =>
        requestAnimationFrame(() => this.bindFlatpickr()),
      )
      // The bulk-edit modal reuses the same .js-flatpickr-* inputs and
      // is also gated behind an x-if, so bind on open the same way.
      this.$watch('bulkEditOpen', () =>
        requestAnimationFrame(() => this.bindFlatpickr()),
      )
    },

    bindFlatpickr() {
      if (!window.flatpickr) return
      const dateFmt =
        DATE_FMT_MAP[metaContent('anthias-date-format')] || 'm/d/Y'
      const use24 = metaContent('anthias-use-24h') === 'true'

      // Alpine seeds the inputs with ISO strings (start_date_local =
      // "2026-05-02T00:00", play_time_from = "09:30") because that's
      // what the server hands us. Parse the seed into a Date here and
      // hand it to Flatpickr via setDate(); leaving the raw ISO string
      // as the input value would make Flatpickr fail to parse it
      // against the user-format mask and display garbage like
      // "08/06/2027" — which then made existing assets fall out of
      // their is_active() window when the form was saved.
      const seedDateTime = (raw: string): Date | null => {
        if (!raw) return null
        const d = new Date(raw)
        return isNaN(d.getTime()) ? null : d
      }
      const seedTimeOnly = (raw: string): Date | null => {
        if (!raw) return null
        const m = raw.match(/^(\d{1,2}):(\d{2})/)
        if (!m) return null
        const d = new Date()
        d.setHours(parseInt(m[1], 10), parseInt(m[2], 10), 0, 0)
        return d
      }

      // js- prefix avoids the collision with flatpickr's own
      // .flatpickr-time internal class (max-height: 40px), which
      // otherwise capped our floating-label inputs at 40px instead
      // of the .app-floating 3.6rem height.
      document
        .querySelectorAll<HTMLInputElement>('.js-flatpickr-datetime')
        .forEach((el) => {
          const fp = (el as { _flatpickr?: { destroy: () => void } })
            ._flatpickr
          if (fp) fp.destroy()
          const seed = seedDateTime(el.value)
          const inst = window.flatpickr(el, {
            enableTime: true,
            time_24hr: use24,
            dateFormat: `${dateFmt} ${use24 ? 'H:i' : 'h:i K'}`,
            allowInput: true,
          })
          if (seed) (inst as { setDate: (d: Date, fire: boolean) => void }).setDate(seed, false)
        })
      document
        .querySelectorAll<HTMLInputElement>('.js-flatpickr-time')
        .forEach((el) => {
          const fp = (el as { _flatpickr?: { destroy: () => void } })
            ._flatpickr
          if (fp) fp.destroy()
          const seed = seedTimeOnly(el.value)
          const inst = window.flatpickr(el, {
            enableTime: true,
            noCalendar: true,
            time_24hr: use24,
            dateFormat: use24 ? 'H:i' : 'h:i K',
            allowInput: true,
          })
          if (seed) (inst as { setDate: (d: Date, fire: boolean) => void }).setDate(seed, false)
        })
    },

    openAdd() {
      this.mode = 'add'
      this.editAsset = null
    },
    openEdit(asset: AssetEdit) {
      this.mode = 'edit'
      this.editAsset = asset
    },
    openPreview(asset: AssetEdit) {
      this.previewAsset = asset
    },
    openDelete(id: string, name: string) {
      this.pendingDeleteId = id
      this.pendingDeleteName = name || ''
    },
    closeModal() {
      // Hiding the modal during an in-flight upload (Hide button, Esc,
      // backdrop click) must NOT touch the upload state. uploadFiles()
      // owns uploadState for the whole batch and clears it when the
      // last file lands; wiping it here mid-batch would disarm the
      // re-entry guard (a reopened modal could start a second batch
      // that races the first over the shared progress/index fields)
      // and tear down the progress UI while files are still uploading.
      // Only reset when nothing is in flight — by then it's a no-op
      // anyway.
      this.mode = null
      this.editAsset = null
      if (!this.uploadState) {
        this.uploadProgress = 0
        this.uploadFileName = ''
        this.uploadIndex = 0
        this.uploadTotal = 0
      }
    },
    closePreview() {
      this.previewAsset = null
    },

    // --- Bulk selection (#3046) ---------------------------------------
    // selectedIds is the source of truth; row checkboxes bind their
    // :checked to isSelected() so the selection survives the table's
    // 5s HTMX swap (the swapped rows re-evaluate against this state).
    isSelected(id) {
      return this.selectedIds.includes(id)
    },
    toggleSelect(id) {
      if (this.selectedIds.includes(id)) {
        this.selectedIds = this.selectedIds.filter((x) => x !== id)
      } else {
        this.selectedIds = [...this.selectedIds, id]
      }
    },
    // Called once from each rendered table partial (x-init re-fires
    // after every swap) so Alpine always knows the ids currently on
    // screen. Both sections are passed together and pruning happens
    // once against their union — pruning per-section would drop the
    // selection for a row that moved between sections (e.g. an enabled
    // asset just disabled), because the other section's list would
    // still be stale at that moment.
    syncVisibleIds(activeIds, inactiveIds) {
      this.visibleIds.active = activeIds
      this.visibleIds.inactive = inactiveIds
      const all = new Set([...activeIds, ...inactiveIds])
      this.selectedIds = this.selectedIds.filter((id) => all.has(id))
    },
    sectionAllSelected(section) {
      const ids = this.visibleIds[section]
      return ids.length > 0 && ids.every((id) => this.selectedIds.includes(id))
    },
    sectionSomeSelected(section) {
      const ids = this.visibleIds[section]
      const n = ids.filter((id) => this.selectedIds.includes(id)).length
      return n > 0 && n < ids.length
    },
    toggleSection(section, checked) {
      const ids = this.visibleIds[section]
      if (checked) {
        const merged = new Set([...this.selectedIds, ...ids])
        this.selectedIds = [...merged]
      } else {
        const drop = new Set(ids)
        this.selectedIds = this.selectedIds.filter((id) => !drop.has(id))
      }
    },
    clearSelection() {
      this.selectedIds = []
    },
    openBulkEdit() {
      this.bulkEditOpen = true
    },
    closeBulkEdit() {
      this.bulkEditOpen = false
    },

    // Multi-file upload (issue #3045). The server's assets_upload
    // endpoint reads exactly one file per request
    // (request.FILES.get('file_upload')), so a single htmx form POST —
    // which would carry every selected file in the multipart body —
    // would still only create one asset. uploadFiles() instead uploads
    // the batch sequentially, one XHR (one file) per request. Driving
    // it from JS (instead of hx-post on the <form>) is also what makes
    // "X of N" progress and per-file failure handling possible. Mirrors
    // the pre-#2818 React behaviour added in #2778.
    async uploadFiles(input: HTMLInputElement) {
      const files = input.files ? Array.from(input.files) : []
      const form = input.form
      // Guard against re-entry: a drop/select while a batch is still
      // in flight would clobber the progress + index state.
      if (!files.length || !form || this.uploadState) {
        input.value = ''
        return
      }
      const url = form.getAttribute('action') || ''
      const csrf =
        form.querySelector<HTMLInputElement>(
          'input[name=csrfmiddlewaretoken]',
        )?.value || csrfToken()

      this.uploadTotal = files.length
      let succeeded = 0
      let aborted = false
      for (let i = 0; i < files.length; i++) {
        this.uploadIndex = i + 1
        this.uploadFileName = files[i].name
        const result = await this.uploadOne(url, csrf, files[i])
        if (result === 'error') {
          // Transport failure — something's wrong with the request
          // itself, so stop the batch rather than hammering on.
          aborted = true
          break
        }
        // 'rejected' files already surfaced their own server toast;
        // skip them and keep uploading the rest of the selection.
        if (result === 'ok') succeeded += 1
      }

      // Clear the input so re-selecting the same file(s) fires change
      // again, then drop the progress UI.
      input.value = ''
      this.uploadState = null
      this.uploadProgress = 0
      this.uploadFileName = ''
      this.uploadIndex = 0
      this.uploadTotal = 0

      if (aborted) {
        const store = window.Alpine.store('toasts') as
          | ToastStoreLike
          | undefined
        store?.push('error', 'Upload failed — check the file and try again')
      }
      if (succeeded > 0) {
        this.mode = null
        // The per-file responses each fan out a WebSocket refresh
        // nudge, but force one final swap so the new rows land
        // immediately even when the socket is unavailable.
        const htmx = (
          window as unknown as {
            htmx?: { trigger: (target: string, event: string) => void }
          }
        ).htmx
        htmx?.trigger('body', 'refresh-assets')
      }
    },

    // POST a single file and resolve an UploadResult: 'ok' on a 2xx
    // with no error toast, 'rejected' on a 2xx carrying an error toast
    // (server refused the file), 'error' on a non-2xx or transport
    // failure. Server toasts ("reading metadata…" / "Uploaded X" /
    // "Invalid file type") ride back on the HX-Trigger header — we
    // replay them here since this isn't an htmx-managed request.
    // Progress flips to "processing" once the bytes are up (the server
    // still has to write to disk + ffprobe).
    uploadOne(
      this: HomeAppData,
      url: string,
      csrf: string,
      file: File,
    ): Promise<UploadResult> {
      return new Promise<UploadResult>((resolve) => {
        const xhr = new XMLHttpRequest()
        xhr.open('POST', url)
        xhr.setRequestHeader('X-CSRFToken', csrf)
        // Mark as an htmx request so assets_upload returns the table
        // partial (+ HX-Trigger toast) instead of a full-page redirect.
        xhr.setRequestHeader('HX-Request', 'true')
        this.uploadState = 'sending'
        this.uploadProgress = 0
        xhr.upload.addEventListener('progress', (ev) => {
          if (!ev.lengthComputable || ev.total === 0) return
          this.uploadProgress = Math.min(
            99,
            Math.round((ev.loaded / ev.total) * 100),
          )
          if (ev.loaded >= ev.total) this.uploadState = 'processing'
        })
        xhr.addEventListener('load', () => {
          const kind = fireToastFromHeader(xhr.getResponseHeader('HX-Trigger'))
          if (xhr.status < 200 || xhr.status >= 300) {
            resolve('error')
            return
          }
          // The server validates and may refuse a file with a 200 +
          // error toast (invalid type, missing file). Treat that as a
          // rejected file, not a silent success.
          resolve(kind === 'error' ? 'rejected' : 'ok')
        })
        xhr.addEventListener('error', () => resolve('error'))
        xhr.addEventListener('abort', () => resolve('error'))
        const fd = new FormData()
        fd.append('csrfmiddlewaretoken', csrf)
        fd.append('file_upload', file)
        xhr.send(fd)
      })
    },
  }
}

// Replay a server HX-Trigger toast payload through the global store
// and return its kind. htmx does this automatically for hx-* requests;
// the file-upload path uses raw XHR (see uploadFiles), so we parse it
// by hand. The returned kind lets uploadOne treat a server-side
// rejection (HTTP 200 + an error toast — e.g. invalid file type) as a
// failed file rather than a silent success.
function fireToastFromHeader(
  header: string | null,
): 'success' | 'error' | 'info' | null {
  if (!header) return null
  try {
    const parsed = JSON.parse(header) as {
      toast?: { kind?: 'success' | 'error' | 'info'; message?: string }
    }
    const toast = parsed?.toast
    if (toast?.message) {
      const store = window.Alpine.store('toasts') as ToastStoreLike | undefined
      store?.push(toast.kind || 'info', toast.message)
      return toast.kind || 'info'
    }
  } catch {
    // Header was a bare event name, not JSON, or carried no toast —
    // nothing to surface.
  }
  return null
}

// Tracks the assets that were `is_processing=true` on the previous
// render of the asset table. After every htmx swap we diff the current
// processing set against this snapshot; any asset that left the set
// (probe finished) gets a "Processing complete" toast with the
// resolved duration. Lives on `window` so the htmx event listener
// installed once at page load can reach it across re-renders.
declare global {
  interface Window {
    __anthiasProcessing?: Set<string>
  }
}

function snapshotProcessing(): Set<string> {
  const next = new Set<string>()
  document
    .querySelectorAll<HTMLTableRowElement>('tr[data-asset-id][data-processing="true"]')
    .forEach((tr) => {
      const id = tr.dataset.assetId
      if (id) next.add(id)
    })
  return next
}

function diffProcessingAndToast(prev: Set<string>): void {
  const store = window.Alpine?.store('toasts') as
    | { push: (k: 'success' | 'error' | 'info', m: string) => number }
    | undefined
  if (!store) return
  // Find rows that *were* processing last time and are no longer in
  // the processing set now. A row that's been entirely removed (e.g.
  // operator deleted it) is also dropped from the set silently — no
  // toast for that since assets_delete already fires its own.
  prev.forEach((id) => {
    const row = document.querySelector<HTMLTableRowElement>(
      `tr[data-asset-id="${CSS.escape(id)}"]`,
    )
    if (!row) return
    if (row.dataset.processing === 'true') return
    const name = row.dataset.name || 'video'
    const duration = parseInt(row.dataset.duration || '0', 10)
    const suffix = duration > 0 ? ` — ${humanizeDuration(duration)}` : ''
    store.push('success', `Analysed ${name}${suffix}`)
  })
}

function humanizeDuration(total: number): string {
  if (total <= 0) return '0s'
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const seconds = total % 60
  if (hours) return minutes ? `${hours}h ${minutes}m` : `${hours}h`
  if (minutes) return seconds ? `${minutes}m ${seconds}s` : `${minutes}m`
  return `${seconds}s`
}

function installProcessingToastWatcher(): void {
  if (window.__anthiasProcessing !== undefined) return
  window.__anthiasProcessing = snapshotProcessing()
  document.body.addEventListener('htmx:afterSwap', (ev) => {
    const target = (ev as CustomEvent<{ target?: Element }>).detail?.target
    if (!target) return
    if (
      !(target instanceof Element) ||
      !target.querySelector('tr[data-asset-id]')
    ) {
      return
    }
    const prev = window.__anthiasProcessing ?? new Set<string>()
    diffProcessingAndToast(prev)
    window.__anthiasProcessing = snapshotProcessing()
  })
}

type HtmxLike = { trigger: (target: string, event: string) => void }

function postOrder(orderUrl: string, tbody: HTMLElement): void {
  const ids = Array.from(tbody.children)
    .map((tr) => (tr as HTMLElement).dataset.assetId)
    .filter(Boolean)
    .join(',')
  const fd = new FormData()
  fd.append('ids', ids)
  const refresh = () =>
    (window as unknown as { htmx: HtmxLike }).htmx.trigger(
      'body',
      'refresh-assets',
    )
  fetch(orderUrl, {
    method: 'POST',
    body: fd,
    headers: {
      'X-CSRFToken': csrfToken(),
      'HX-Request': 'true',
    },
  })
    .then((r) => {
      if (!r.ok) {
        console.error('reorder POST failed:', r.status, r.statusText)
      }
      refresh()
    })
    .catch((err) => {
      console.error('reorder POST errored:', err)
      refresh()
    })
}

// Vanilla pointer-events drag-to-reorder. We intentionally don't use
// SortableJS — its <tr> handling kept silently failing under what
// looked like correct configuration, and the whole library is
// overkill for "swap rows in one tbody and POST the new order".
//
// The dragged <tr> stays in place while the cursor moves; on every
// pointermove we find the row under the cursor and reinsert the
// dragged row before/after it based on which half of the row the
// cursor is over. On pointerup we POST the resulting id sequence.
function bindActiveRowsDrag(tbody: HTMLElement, orderUrl: string): void {
  if (tbody.dataset.dragBound === '1') return
  tbody.dataset.dragBound = '1'

  let dragRow: HTMLTableRowElement | null = null
  let pointerId = -1
  let moved = false

  const cleanup = (): void => {
    document.removeEventListener('pointermove', onMove)
    document.removeEventListener('pointerup', onUp)
    document.removeEventListener('pointercancel', onUp)
    if (dragRow) {
      dragRow.classList.remove('is-dragging')
      dragRow = null
    }
  }

  const onMove = (ev: PointerEvent): void => {
    if (!dragRow || ev.pointerId !== pointerId) return
    const overEl = document.elementFromPoint(ev.clientX, ev.clientY)
    const overRow = overEl?.closest('tr') as HTMLTableRowElement | null
    if (!overRow || overRow === dragRow) return
    if (overRow.parentElement !== tbody) return
    const rect = overRow.getBoundingClientRect()
    const before = ev.clientY < rect.top + rect.height / 2
    tbody.insertBefore(dragRow, before ? overRow : overRow.nextSibling)
    moved = true
  }

  const onUp = (ev: PointerEvent): void => {
    if (ev.pointerId !== pointerId) return
    const didMove = moved
    cleanup()
    if (didMove) postOrder(orderUrl, tbody)
  }

  tbody.addEventListener('pointerdown', (ev: PointerEvent) => {
    if (ev.button !== 0) return
    const handle = (ev.target as HTMLElement).closest('.drag-handle')
    if (!handle) return
    const row = handle.closest('tr') as HTMLTableRowElement | null
    if (!row || row.parentElement !== tbody) return

    ev.preventDefault()
    dragRow = row
    pointerId = ev.pointerId
    moved = false
    dragRow.classList.add('is-dragging')

    document.addEventListener('pointermove', onMove)
    document.addEventListener('pointerup', onUp)
    document.addEventListener('pointercancel', onUp)
  })
}

function setupActiveRowsDrag(): void {
  const wrapper = document.getElementById('asset-table')
  if (!wrapper) return
  const orderUrl = wrapper.dataset.orderUrl
  if (!orderUrl) return
  const tbody = document.getElementById('active-rows')
  if (!tbody) return
  bindActiveRowsDrag(tbody, orderUrl)
}

// Plain-HTTP clipboard fallback. navigator.clipboard.writeText only
// resolves on secure origins (HTTPS or localhost); Anthias devices
// serve the dashboard over plain HTTP on the LAN by default, so
// invoking writeText() there throws "writeText is not a function" or
// rejects with SecurityError. The deprecated execCommand('copy') path
// still works from a user gesture in every browser we ship to.
function fallbackCopyToClipboard(text: string): boolean {
  const ta = document.createElement('textarea')
  ta.value = text
  // Off-screen but in the layout, so .select() works without
  // flashing the input.
  ta.setAttribute('readonly', '')
  ta.style.position = 'fixed'
  ta.style.top = '0'
  ta.style.left = '0'
  ta.style.opacity = '0'
  document.body.appendChild(ta)
  ta.select()
  ta.setSelectionRange(0, text.length)
  let ok = false
  try {
    ok = document.execCommand('copy')
  } catch {
    ok = false
  }
  document.body.removeChild(ta)
  return ok
}
window.fallbackCopyToClipboard = fallbackCopyToClipboard

window.homeApp = homeApp

function bootHomePage(): void {
  installProcessingToastWatcher()
  setupActiveRowsDrag()
  document.body.addEventListener('htmx:afterSwap', (ev) => {
    const target = (ev as CustomEvent<{ target?: Element }>).detail?.target
    if (!target) return
    if (target instanceof Element && target.querySelector('#active-rows')) {
      setupActiveRowsDrag()
    }
  })
}

// Mirrors the vendor.ts pattern: subscribe to BOTH DOMContentLoaded
// and load so a dynamically-injected bundle (readyState already
// 'interactive', DCL already fired) still boots via the load event.
// The `booted` guard de-dupes the normal-load case where both fire.
let booted = false
function bootOnce(): void {
  if (booted) return
  booted = true
  bootHomePage()
}

if (document.readyState === 'complete') {
  bootOnce()
} else {
  document.addEventListener('DOMContentLoaded', bootOnce, { once: true })
  window.addEventListener('load', bootOnce, { once: true })
}
