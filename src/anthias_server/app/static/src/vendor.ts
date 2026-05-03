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
import htmx from 'htmx.org'
import Alpine from 'alpinejs'
import Sortable from 'sortablejs'
import flatpickr from 'flatpickr'

declare global {
  interface Window {
    htmx: typeof htmx
    Alpine: typeof Alpine
    Sortable: typeof Sortable
    flatpickr: typeof flatpickr
  }
}

// htmx ships an IIFE (var htmx = (function(){...})(); export default htmx).
// When bun bundles it, the IIFE's internal var stays module-scoped, so
// `window.htmx` is undefined unless we assign it explicitly. Inline scripts
// in templates and home.ts both reach for window.htmx.trigger(...) and would
// throw a TypeError without this line.
window.htmx = htmx
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
// htmx 2.x dispatches the named event on the *triggering* element
// (the form/button), so we listen at the document level — events
// bubble (htmx sets bubbles: true on its CustomEvents), and document
// catches anything regardless of whether the trigger element survived
// the swap. Listening on document.body would miss frames where the
// triggering element was already detached before the bubble reached.
const handleToast = (ev: Event): void => {
  const detail = (ev as CustomEvent<{
    kind?: ToastItem['kind']
    message?: string
  }>).detail
  if (!detail?.message) {
    return
  }
  ;(Alpine.store('toasts') as ToastStore).push(
    detail.kind ?? 'info',
    detail.message,
  )
}
document.addEventListener('toast', handleToast as EventListener)

// WebSocket fan-out from the Channels AssetConsumer. The server
// triggers `htmx.trigger('body', 'refresh-assets')` indirectly: on
// every Asset write the view fans a small message over /ws, this
// listener picks it up and asks htmx to re-fetch the table partial
// immediately (rather than waiting for the 5s poll). Falls back to
// the poll if the socket isn't reachable — so this is purely an
// optimisation, never a correctness dependency.
function connectAssetSocket(): void {
  if (!('WebSocket' in window)) return
  const proto = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const url = `${proto}//${window.location.host}/ws`
  let socket: WebSocket | null = null
  let backoff = 1000
  const open = (): void => {
    try {
      socket = new WebSocket(url)
    } catch {
      // URL or environment refused — give up; the poll covers it.
      return
    }
    socket.addEventListener('open', () => {
      backoff = 1000
    })
    socket.addEventListener('message', () => {
      const htmx = (window as unknown as {
        htmx?: { trigger: (...args: unknown[]) => void }
      }).htmx
      htmx?.trigger('body', 'refresh-assets')
    })
    socket.addEventListener('close', () => {
      // Reconnect with capped exponential backoff so a transient
      // server restart doesn't leave the page stuck on poll-only.
      const delay = Math.min(backoff, 15000)
      backoff = Math.min(backoff * 2, 15000)
      window.setTimeout(open, delay)
    })
    socket.addEventListener('error', () => {
      // Triggers a 'close' right after; let the close handler manage
      // backoff so we don't double-schedule the reconnect.
      socket?.close()
    })
  }
  open()
}

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
  connectAssetSocket()
} else {
  document.addEventListener(
    'DOMContentLoaded',
    () => {
      Alpine.start()
      drainDjangoMessages()
      connectAssetSocket()
    },
    { once: true },
  )
}
