import { afterEach } from 'bun:test'
import { GlobalRegistrator } from '@happy-dom/global-registrator'

// happy-dom ships its own `Fetch` class and overwrites `globalThis.fetch` on
// register. MSW's node interceptor patches `globalThis.fetch`, but happy-dom's
// internal fetch path bypasses that patched function, so requests escape to
// the real network (ECONNREFUSED in tests). Workaround: stash Bun's native
// fetch before registration and restore it after, so MSW can hook it cleanly.
const bunFetch = globalThis.fetch

GlobalRegistrator.register({ url: 'http://localhost/' })

globalThis.fetch = bunFetch

// Dynamic imports: `@testing-library/dom` captures `document.body` into its
// `screen` export at module-load time. A static import at the top of this
// file would evaluate `screen` before `GlobalRegistrator.register()` runs,
// binding it to an undefined body — every `screen.getByText(...)` would then
// throw "For queries bound to document.body a global document has to be
// available". Delaying these imports until after register fixes it.
const { cleanup } = await import('@testing-library/react')
await import('@testing-library/jest-dom')

afterEach(() => {
  cleanup()
})
