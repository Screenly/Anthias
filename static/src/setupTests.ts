import { afterEach } from 'bun:test'
import { GlobalRegistrator } from '@happy-dom/global-registrator'

const bunFetch = globalThis.fetch

GlobalRegistrator.register({ url: 'http://localhost/' })

globalThis.fetch = bunFetch

const { cleanup } = await import('@testing-library/react')
await import('@testing-library/jest-dom')

afterEach(() => {
  cleanup()
})
