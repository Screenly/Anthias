// Self-hosted vendored bundle for the post-React stack: htmx for
// server-driven interactivity, Alpine for the few client-only state
// sprinkles (modal open/close, mobile-nav collapse), Sortable for
// drag-to-reorder on the home page, Flatpickr for locale-aware
// date/time inputs in the edit modal. Loaded by base.html via
// /static/dist/js/vendor.js, before any page-specific script.
//
// Importing htmx.org for its side effect attaches `htmx` to window
// and wires the global DOMContentLoaded listener. The other libs
// don't auto-expose, so we attach them explicitly so inline scripts
// in templates can reach them without going through a module bundler.
import 'htmx.org'
import Alpine from 'alpinejs'
import Sortable from 'sortablejs'
import flatpickr from 'flatpickr'

declare global {
  interface Window {
    Alpine: typeof Alpine
    Sortable: typeof Sortable
    flatpickr: typeof flatpickr
  }
}

window.Alpine = Alpine
window.Sortable = Sortable
window.flatpickr = flatpickr

interface ToastItem {
  id: number
  kind: 'success' | 'error' | 'info'
  message: string
}

interface ToastStore {
  items: ToastItem[]
  nextId: number
  push(kind: ToastItem['kind'], message: string, ttlMs?: number): number
  dismiss(id: number): void
}

// Global toast store — every page loads this. Endpoints can fire
// `HX-Trigger: {"toast": {"kind":"success","message":"…"}}` and the
// listener below converts that to a Toast item. Django flash messages
// (settings save, backup recover, etc.) are drained on DCL via the
// embedded <script id="django-messages" type="application/json"> tag.
Alpine.store('toasts', {
  items: [] as ToastItem[],
  nextId: 1,
  push(kind, message, ttlMs = 4000) {
    const id = this.nextId++
    this.items.push({ id, kind, message })
    if (ttlMs > 0) {
      window.setTimeout(() => this.dismiss(id), ttlMs)
    }
    return id
  },
  dismiss(id: number) {
    this.items = this.items.filter((t: ToastItem) => t.id !== id)
  },
} as ToastStore)

// HX-Trigger fan-out. The server attaches a JSON header
//   HX-Trigger: {"toast": {"kind":"success", "message":"Asset deleted"}}
// HTMX dispatches a custom event named `toast` on the body with that
// detail payload; we wire it into the global store.
document.body.addEventListener('toast', ((ev: Event) => {
  const detail = (ev as CustomEvent<{ kind?: ToastItem['kind']; message?: string }>).detail
  if (!detail || !detail.message) return
  ;(Alpine.store('toasts') as ToastStore).push(
    detail.kind ?? 'info',
    detail.message,
  )
}) as EventListener)

// Drain server-rendered Django flash messages once the DOM is up.
function drainDjangoMessages(): void {
  const node = document.getElementById('django-messages')
  if (!node || !node.textContent) return
  try {
    const parsed = JSON.parse(node.textContent) as Array<{
      tags?: string
      message: string
    }>
    parsed.forEach((m) => {
      const tag = (m.tags || '').toLowerCase()
      const kind: ToastItem['kind'] =
        tag.includes('error') || tag.includes('danger')
          ? 'error'
          : tag.includes('success')
            ? 'success'
            : 'info'
      ;(Alpine.store('toasts') as ToastStore).push(kind, m.message)
    })
  } catch {
    // The script tag is missing or malformed — swallow rather than
    // breaking the rest of the page over a flash-message edge case.
  }
}

// Defer Alpine.start() until DOMContentLoaded so per-page bundles
// (loaded as separate `defer` <script>s) get to define their
// components — `window.homeApp`, etc. — BEFORE Alpine scans the DOM
// and tries to evaluate `x-data="homeApp()"`. Without this guard
// vendor.js (which loads first under defer) would call Alpine.start()
// at parse time; at that point readyState is already 'interactive'
// but home.js hasn't been executed yet, and every x-data blows up
// with "homeApp is not defined". Wait for the actual DCL event
// instead — that fires only after every other defer script has run.
if (document.readyState === 'complete') {
  Alpine.start()
  drainDjangoMessages()
} else {
  document.addEventListener(
    'DOMContentLoaded',
    () => {
      Alpine.start()
      drainDjangoMessages()
    },
    { once: true },
  )
}
