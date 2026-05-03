// Home page (Schedule Overview) client logic. Exposes `homeApp()`
// for the Alpine x-data on home.html and the `initAssetTableSortable`
// helper that the asset-table partial calls from a tiny inline script
// (the `{% url 'anthias_app:assets_order' %}` value is the only
// reason that line stays in the template; everything else lives here
// so we get TypeScript type-checking instead of debugging inline
// strings via devtools).
//
// Loaded by home.html as a deferred bundle alongside vendor.js, so
// window.flatpickr / window.Sortable are guaranteed to exist by the
// time `requestAnimationFrame` fires the modal binding callback.

import type Alpine from 'alpinejs'
import type SortableLib from 'sortablejs'
import type flatpickrLib from 'flatpickr'

declare global {
  interface Window {
    Alpine: typeof Alpine
    Sortable: typeof SortableLib
    flatpickr: typeof flatpickrLib
    homeApp: () => HomeAppData
    initAssetTableSortable: (orderUrl: string) => void
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

      document
        .querySelectorAll<HTMLInputElement>('.flatpickr-datetime')
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
        .querySelectorAll<HTMLInputElement>('.flatpickr-time')
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

// Bind Sortable on the active-rows tbody. Idempotent: each call
// destroys any existing instance on the tbody before creating a fresh
// one, so we can call this from both DOMContentLoaded and the
// htmx:afterSwap watcher without leaking listeners.
function bindActiveRowsSortable(): void {
  if (!window.Sortable) return
  const wrapper = document.getElementById('asset-table')
  if (!wrapper) return
  const orderUrl = wrapper.dataset.orderUrl
  if (!orderUrl) return
  const tbody = document.getElementById('active-rows')
  if (!tbody) return
  // Sortable.js stamps the element with an internal `_sortable`
  // reference; re-creating without destroying first stacks listeners
  // on document and breaks subsequent drags after a swap.
  const existing = (
    window.Sortable as { get?: (el: HTMLElement) => { destroy: () => void } | null }
  ).get?.(tbody)
  if (existing) existing.destroy()
  new window.Sortable(tbody, {
    handle: '.drag-handle',
    animation: 150,
    onEnd: () => {
      const ids = Array.from(tbody.children)
        .map((tr) => (tr as HTMLElement).dataset.assetId)
        .filter(Boolean)
        .join(',')
      const fd = new FormData()
      fd.append('ids', ids)
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
          ;(
            window as unknown as { htmx: { trigger: (...args: unknown[]) => void } }
          ).htmx.trigger('body', 'refresh-assets')
        })
        .catch((err) => {
          console.error('reorder POST errored:', err)
          ;(
            window as unknown as { htmx: { trigger: (...args: unknown[]) => void } }
          ).htmx.trigger('body', 'refresh-assets')
        })
    },
  })
}

// Compatibility shim for any external caller that still uses the
// previous explicit URL-passing entry point. Internally we now read
// the URL off the data attribute, so the arg is ignored.
function initAssetTableSortable(_orderUrl?: string): void {
  bindActiveRowsSortable()
}

window.homeApp = homeApp
window.initAssetTableSortable = initAssetTableSortable

// Bind Sortable on initial page render and re-bind after every
// htmx swap that brings a new active-rows tbody. The previous
// approach used an inline <script> at the end of the asset-table
// partial which raced with home.js (defer): on initial parse the
// inline script ran before window.initAssetTableSortable was
// defined and Sortable never bound until the first 5s poll.
function bootHomePage(): void {
  installProcessingToastWatcher()
  bindActiveRowsSortable()
  document.body.addEventListener('htmx:afterSwap', (ev) => {
    const target = (ev as CustomEvent<{ target?: Element }>).detail?.target
    if (!target) return
    if (target instanceof Element && target.querySelector('#active-rows')) {
      bindActiveRowsSortable()
    }
  })
}

if (document.readyState === 'complete') {
  bootHomePage()
} else {
  document.addEventListener('DOMContentLoaded', bootHomePage, { once: true })
}
