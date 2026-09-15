// The Add modal is hidden with x-show, never unmounted, so everything
// the last Add left in it — the typed URL, the tab, the Apps pane's
// config form — survives a close. openAdd() is what wipes it.
//
// These mount the real Alpine against the real modal markup rather than
// calling openAdd() directly: the reset only works if `x-model="addUri"`
// writes through to the DOM input, if `tab` resolves up the scope chain
// from inside appsTab()'s nested x-data, and if the pane's
// @add-modal-open.window listener is actually wired. None of that is
// observable from the component object alone.

import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  test,
} from 'bun:test'
import Alpine from 'alpinejs'

import type { AppsTabData } from './apps'
import './home'

const TEMPLATE_PATH = new URL(
  '../../templates/_asset_modal.html',
  import.meta.url,
).pathname

// The panes the reset touches, trimmed to the attributes under test.
// A bun test can't render a Django template, so this is a hand-written
// reduction of _asset_modal.html — which means it could drift from the
// real markup and keep passing while production is broken. The
// "matches the shipped template" cases at the bottom close that gap:
// every Alpine attribute this fixture leans on is asserted to still be
// in the template it stands in for.
const MODAL = `
  <div x-data="homeApp()" @asset-saved.window="closeModal()">
    <button id="add-asset-button" @click="openAdd()"></button>
    <div x-show="mode">
      <div x-show="mode === 'add'">
        <button id="tab-uri" @click="tab = 'uri'"></button>
        <button id="tab-file" @click="tab = 'file'"></button>
        <form x-show="tab === 'uri'">
          <input type="url" id="add-uri" name="uri" required x-model="addUri">
        </form>
        <div id="apps-pane" x-show="tab === 'apps'" x-data="appsTab()"
             x-effect="if (tab === 'apps') load()"
             @add-modal-open.window="reset()">
          <form x-show="phase === 'config'">
            <input type="text" id="app-asset-name" name="name" required
                   x-model="assetName" @input="nameEdited = true">
            <div x-ref="configHost"></div>
          </form>
        </div>
      </div>
    </div>
  </div>`

interface ModalState {
  mode: 'add' | 'edit' | null
  tab: 'uri' | 'file' | 'apps'
  addUri: string
  uploadState: null | 'sending' | 'processing'
}

let state: ModalState
let apps: AppsTabData
let uriInput: HTMLInputElement
let nameInput: HTMLInputElement

// Alpine schedules its reactive effects on a microtask queue.
const settle = (): Promise<void> =>
  new Promise((resolve) => setTimeout(resolve, 0))

// Alpine may only be start()ed once per page; every case after the
// first re-mounts the swapped-in DOM with initTree instead.
let started = false

beforeEach(async () => {
  document.body.innerHTML = MODAL
  ;(window as unknown as { Alpine: unknown }).Alpine = Alpine
  if (started) {
    Alpine.initTree(document.body)
  } else {
    Alpine.start()
    started = true
  }
  await settle()
  state = Alpine.$data(
    document.querySelector('[x-data*="homeApp"]')!,
  ) as unknown as ModalState
  apps = Alpine.$data(
    document.getElementById('apps-pane')!,
  ) as unknown as AppsTabData
  uriInput = document.getElementById('add-uri') as HTMLInputElement
  nameInput = document.getElementById('app-asset-name') as HTMLInputElement
})

// Drop the tree's effects and window listeners, so the next case's
// openAdd() can't reach the last one's components.
afterEach(() => {
  Alpine.destroyTree(document.body)
  document.body.innerHTML = ''
  delete (window as unknown as { Alpine?: unknown }).Alpine
})

function openAddModal(): void {
  document.getElementById('add-asset-button')!.click()
}

function typeUrl(value: string): void {
  uriInput.value = value
  uriInput.dispatchEvent(new Event('input'))
}

describe('the Add modal opens clean', () => {
  test('a second open drops the previous asset URL', async () => {
    openAddModal()
    await settle()
    typeUrl('https://engadget.com')
    await settle()
    expect(state.addUri).toBe('https://engadget.com')

    window.dispatchEvent(new CustomEvent('asset-saved'))
    await settle()
    expect(state.mode).toBeNull()

    openAddModal()
    await settle()
    expect(state.addUri).toBe('')
    // The assertion that matters: the box the operator looks at.
    expect(uriInput.value).toBe('')
  })

  test('a second open lands back on the From URL tab', async () => {
    openAddModal()
    await settle()
    document.getElementById('tab-file')!.click()
    await settle()
    expect(state.tab).toBe('file')

    window.dispatchEvent(new CustomEvent('asset-saved'))
    await settle()
    openAddModal()
    await settle()

    expect(state.tab).toBe('uri')
  })

  // Hiding the modal mid-batch leaves the upload running, and its
  // progress lives on the file tab. The tab strip stays clickable
  // during a batch, so where the operator happened to be when they hid
  // the modal says nothing about where the progress is.
  test('reopening during an upload goes to the file tab', async () => {
    openAddModal()
    await settle()
    document.getElementById('tab-file')!.click()
    state.uploadState = 'sending'
    // Wander off to the URL tab, then hide the modal.
    document.getElementById('tab-uri')!.click()
    state.mode = null
    await settle()

    openAddModal()
    await settle()
    expect(state.tab).toBe('file')
    // The URL field is unrelated to the batch, so it still resets.
    expect(uriInput.value).toBe('')
  })

  test('a second open leaves the Apps pane on the catalog', async () => {
    apps.loaded = true
    apps.phase = 'config'
    apps.launchUrl = 'https://apps.example.com/clock?tz=UTC'
    openAddModal()
    await settle()

    expect(apps.phase).toBe('ready')
    expect(apps.launchUrl).toBe('')
  })

  // An empty or unreachable catalog is a state load() will not
  // re-derive: it sets `loaded`, so a pane bounced to 'ready' shows an
  // empty grid with no message and no refetch. Only the config phase is
  // the reset's to unwind.
  test('a second open keeps an app-store error on screen', async () => {
    apps.loaded = true
    apps.phase = 'error'
    apps.error = 'No apps are available right now.'
    openAddModal()
    await settle()

    expect(apps.phase).toBe('error')
    expect(apps.error).toBe('No apps are available right now.')
  })
})

// Same defect, one control over: the install form's Name box used a
// one-way `:value` bind, so the operator's typing drifted the DOM away
// from `assetName` with nothing to pull it back. Re-selecting the same
// app re-seeds the identical string — no reactive change, no re-render
// — and the previous install's name was still sitting there.
describe('the Apps install form seeds a fresh name', () => {
  const CLOCK = {
    id: 'clock',
    manifestUrl: 'https://store.example/clock.json',
    manifest: {
      id: 'clock',
      manifestVersion: '1',
      name: 'Clock',
      launch: { baseUrl: 'https://apps.example/clock' },
    },
  } as unknown as Parameters<AppsTabData['select']>[0]

  test('re-selecting the same app drops the last typed name', async () => {
    apps.select(CLOCK)
    await settle()
    expect(nameInput.value).toBe('Clock')

    nameInput.value = 'Lobby clock'
    nameInput.dispatchEvent(new Event('input'))
    await settle()
    // The rename has to reach state, or the next seed has nothing to
    // differ from.
    expect(apps.assetName).toBe('Lobby clock')
    expect(apps.nameEdited).toBe(true)

    apps.back()
    await settle()
    apps.select(CLOCK)
    await settle()

    expect(nameInput.value).toBe('Clock')
    expect(apps.nameEdited).toBe(false)
  })
})

// The fixture above is only evidence about production if it still
// mirrors it. These read the shipped template and assert the exact
// attributes the cases above depend on, so dropping or renaming one in
// the Django markup fails the suite rather than quietly narrowing it.
describe('the fixture matches the shipped template', () => {
  let template: string

  beforeAll(async () => {
    template = await Bun.file(TEMPLATE_PATH).text()
  })

  test('the Asset URL box is two-way bound', () => {
    expect(template).toContain('id="add-uri"')
    expect(template).toMatch(/id="add-uri"[^>]*x-model="addUri"/)
  })

  test('the Apps install Name box is two-way bound', () => {
    expect(template).toMatch(/id="app-asset-name"[^>]*x-model="assetName"/)
  })

  test('the Apps pane listens for the reopen', () => {
    expect(template).toContain('@add-modal-open.window="reset()"')
  })

  // The whole point of hoisting `tab` into homeApp() was that a nested
  // x-data would put it out of openAdd()'s reach again.
  test('the Add pane does not re-declare tab in a nested x-data', () => {
    expect(template).not.toMatch(/x-data="\{[^"]*\btab\b/)
  })
})
