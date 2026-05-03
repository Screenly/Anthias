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
} else {
  document.addEventListener('DOMContentLoaded', () => Alpine.start(), {
    once: true,
  })
}
