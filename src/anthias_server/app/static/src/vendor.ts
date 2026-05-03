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
Alpine.start()
