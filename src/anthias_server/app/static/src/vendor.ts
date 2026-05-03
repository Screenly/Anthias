// Self-hosted vendored bundle for the post-React stack: htmx for
// server-driven interactivity, Alpine for the few client-only state
// sprinkles (modal open/close, mobile-nav collapse), Sortable for
// drag-to-reorder on the home page. Loaded by base.html via
// /static/dist/js/vendor.js, before any page-specific script.
//
// Importing htmx.org for its side effect attaches `htmx` to window
// and wires the global DOMContentLoaded listener. Alpine and Sortable
// don't auto-expose, so we attach them explicitly so inline scripts
// in templates can reach them without going through a module bundler.
import 'htmx.org'
import Alpine from 'alpinejs'
import Sortable from 'sortablejs'

declare global {
  interface Window {
    Alpine: typeof Alpine
    Sortable: typeof Sortable
  }
}

window.Alpine = Alpine
window.Sortable = Sortable
Alpine.start()
