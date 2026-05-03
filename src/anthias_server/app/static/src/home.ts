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

function initAssetTableSortable(orderUrl: string): void {
  // Sortable on the active list. Re-init on every HTMX swap (the
  // tbody is replaced wholesale). Drag-end POSTs the new id order
  // back to /assets/order; the response triggers refresh-assets so
  // the table re-fetches with the persisted order. vendor.js loads
  // with `defer`, so on the *initial* page render this runs before
  // window.Sortable is defined — fall back to DOMContentLoaded in
  // that case; HTMX-driven re-renders see Sortable already loaded.
  const init = () => {
    const tbody = document.getElementById('active-rows')
    if (!tbody || !window.Sortable) return
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
          // `HX-Request: true` makes the Django view return the
          // re-rendered partial instead of redirecting to / and
          // forcing fetch() to download the whole page only to
          // discard it.
          headers: {
            'X-CSRFToken': csrfToken(),
            'HX-Request': 'true',
          },
        })
          .then((r) => {
            if (!r.ok) {
              console.error('reorder POST failed:', r.status, r.statusText)
            }
            // htmx is exposed by vendor.js (htmx.org auto-attaches).
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
  if (window.Sortable) {
    init()
  } else {
    document.addEventListener('DOMContentLoaded', init, { once: true })
  }
}

window.homeApp = homeApp
window.initAssetTableSortable = initAssetTableSortable
