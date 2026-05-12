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

interface HomeAppData {
  mode: 'add' | 'edit' | null
  editAsset: AssetEdit | null
  previewAsset: AssetEdit | null
  pendingDeleteId: string | null
  pendingDeleteName: string
  uploadState: UploadState
  uploadProgress: number
  uploadFileName: string
  init(): void
  openAdd(): void
  openEdit(asset: AssetEdit): void
  openPreview(asset: AssetEdit): void
  openDelete(id: string, name: string): void
  closeModal(): void
  closePreview(): void
  bindFlatpickr(): void
  onUploadStart(): void
  onUploadProgress(ev: CustomEvent<ProgressEvent>): void
  onUploadDone(ev: CustomEvent<{ successful: boolean; xhr: XMLHttpRequest }>): void
}

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

    init(this: HomeAppData & { $watch: (k: string, cb: () => void) => void }) {
      // Re-bind Flatpickr every time the edit modal opens. The
      // <template x-if> doesn't mount its children until the
      // expression is true, so defer to the next animation frame
      // (after Alpine has actually inserted the form into the DOM)
      // before querying for inputs.
      this.$watch('editAsset', () =>
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
      // The Hide button stays clickable while the upload bytes are
      // still going up — that just hides the modal. Once the bytes are
      // sent and the server is processing, dropping the modal would
      // strip the form HTMX is still waiting for, so the button is
      // disabled in the template at that point.
      this.mode = null
      this.editAsset = null
      if (this.uploadState !== 'processing') {
        this.uploadState = null
        this.uploadProgress = 0
        this.uploadFileName = ''
      }
    },
    closePreview() {
      this.previewAsset = null
    },
    onUploadStart() {
      this.uploadState = 'sending'
      this.uploadProgress = 0
    },
    onUploadProgress(ev) {
      const detail = ev.detail
      if (!detail || !detail.lengthComputable || detail.total === 0) return
      const pct = Math.min(99, Math.round((detail.loaded / detail.total) * 100))
      this.uploadProgress = pct
      // Once bytes hit the server we flip to "processing" — the server
      // still has to write the file to disk and (for videos) shell out
      // to ffprobe, which is the longest part of the round-trip.
      if (detail.loaded >= detail.total) {
        this.uploadState = 'processing'
      }
    },
    onUploadDone(ev) {
      const ok = ev.detail?.successful
      // Success toast is fired by the server via HX-Trigger so we
      // don't double up here. The client only owns the failure path
      // (transport errors that never reach the server) and the
      // modal-close + state-reset bookkeeping.
      this.uploadState = null
      this.uploadProgress = 0
      if (ok) {
        this.uploadFileName = ''
        this.mode = null
      } else {
        const store = window.Alpine.store('toasts') as
          | ToastStoreLike
          | undefined
        store?.push('error', 'Upload failed — check the file and try again')
      }
    },
  }
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
