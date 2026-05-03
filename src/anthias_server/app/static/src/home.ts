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

interface HomeAppData {
  mode: 'add' | 'edit' | null
  editAsset: AssetEdit | null
  pendingDeleteId: string | null
  pendingDeleteName: string
  init(): void
  openAdd(): void
  openEdit(asset: AssetEdit): void
  openDelete(id: string, name: string): void
  closeModal(): void
  bindFlatpickr(): void
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
    pendingDeleteId: null,
    pendingDeleteName: '',

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
      document
        .querySelectorAll<HTMLInputElement>('.flatpickr-datetime')
        .forEach((el) => {
          const fp = (el as { _flatpickr?: { destroy: () => void } })
            ._flatpickr
          if (fp) fp.destroy()
          window.flatpickr(el, {
            enableTime: true,
            time_24hr: use24,
            dateFormat: `${dateFmt} ${use24 ? 'H:i' : 'h:i K'}`,
            allowInput: true,
          })
        })
      document
        .querySelectorAll<HTMLInputElement>('.flatpickr-time')
        .forEach((el) => {
          const fp = (el as { _flatpickr?: { destroy: () => void } })
            ._flatpickr
          if (fp) fp.destroy()
          window.flatpickr(el, {
            enableTime: true,
            noCalendar: true,
            time_24hr: use24,
            dateFormat: use24 ? 'H:i' : 'h:i K',
            allowInput: true,
          })
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
    openDelete(id: string, name: string) {
      this.pendingDeleteId = id
      this.pendingDeleteName = name || ''
    },
    closeModal() {
      this.mode = null
      this.editAsset = null
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
