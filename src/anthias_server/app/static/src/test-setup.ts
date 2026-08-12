// Registers a DOM on the global scope for `bun test`, so the modules
// that build form controls (manifest-form and friends) can be exercised
// the way the browser runs them. Loaded via `preload` in bunfig.toml,
// which runs before any test file's imports — Leaflet and the widget
// code both touch `window`/`document` at import time.
import { GlobalRegistrator } from '@happy-dom/global-registrator'

GlobalRegistrator.register()
