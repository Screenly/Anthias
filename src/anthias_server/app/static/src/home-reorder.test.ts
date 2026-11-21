import { afterEach, beforeEach, expect, test } from 'bun:test'

import './home'

const originalFetch = globalThis.fetch
const originalHtmx = window.htmx
let savedOrders: string[]
let respond: (() => void)[]
let holdPosts: boolean

function table(order = ['a', 'b', 'c']): string {
  return `<div id="asset-table" data-order-url="/order"><table><tbody id="active-rows">${order.map((id) => `<tr data-asset-id="${id}"><td><button class="drag-handle">${id}</button></td></tr>`).join('')}</tbody></table></div>`
}

function startRequest(): XMLHttpRequest {
  const xhr = new XMLHttpRequest()
  document.body.dispatchEvent(new CustomEvent('htmx:beforeRequest', { detail: { xhr } }))
  return xhr
}

function swap(xhr: XMLHttpRequest): boolean {
  const target = document.getElementById('asset-table')!
  const detail = { target, xhr, shouldSwap: true }
  document.body.dispatchEvent(new CustomEvent('htmx:beforeSwap', { detail }))
  if (!detail.shouldSwap) return false
  target.outerHTML = table()
  document.body.dispatchEvent(new CustomEvent('htmx:afterSwap', {
    detail: { target: document.getElementById('asset-table')! },
  }))
  return true
}

function move(id: string, key = 'ArrowDown'): void {
  const handle = document.querySelector<HTMLButtonElement>(`tr[data-asset-id="${id}"] button`)!
  handle.focus()
  handle.dispatchEvent(new KeyboardEvent('keydown', { key, bubbles: true, cancelable: true }))
}

beforeEach(() => {
  savedOrders = []
  respond = []
  holdPosts = false
  document.body.innerHTML = table()
  window.htmx = { trigger: () => { swap(startRequest()) } } as typeof window.htmx
  Object.defineProperty(globalThis, 'fetch', {
    configurable: true,
    value: (_url: string, options: RequestInit) => {
      savedOrders.push(String((options.body as FormData).get('ids')))
      return new Promise<Response>((resolve) => {
        const done = () => resolve(new Response('', { status: 200 }))
        if (holdPosts) respond.push(done)
        else done()
      })
    },
  })
  document.dispatchEvent(new Event('DOMContentLoaded'))
  document.body.dispatchEvent(new CustomEvent('htmx:afterSwap', {
    detail: { target: document.getElementById('asset-table')! },
  }))
})

afterEach(() => {
  globalThis.fetch = originalFetch
  window.htmx = originalHtmx
  document.body.innerHTML = ''
})

test('a poll during the debounce cannot consume reorder focus', async () => {
  move('a')
  expect(swap(startRequest())).toBe(false)
  await Bun.sleep(550)
  expect(savedOrders).toEqual(['b,a,c'])
  expect(document.activeElement?.closest('tr')?.dataset.assetId).toBe('a')
})

test('a response requested before a move cannot overwrite the saved table', async () => {
  const stale = startRequest()
  move('a')
  await Bun.sleep(550)
  expect(swap(stale)).toBe(false)
  expect(document.activeElement?.closest('tr')?.dataset.assetId).toBe('a')
})

test('a response requested during a move stays stale after saving', async () => {
  move('a')
  const stale = startRequest()
  await Bun.sleep(550)
  expect(swap(stale)).toBe(false)
  expect(document.activeElement?.closest('tr')?.dataset.assetId).toBe('a')
})

test('moves during a slow POST are saved in sequence before refreshing', async () => {
  holdPosts = true
  move('a')
  await Bun.sleep(550)
  expect(savedOrders).toEqual(['b,a,c'])
  move('a')
  await Bun.sleep(550)
  expect(savedOrders).toHaveLength(1)
  expect(swap(startRequest())).toBe(false)
  respond.shift()!()
  await Bun.sleep(0)
  expect(savedOrders).toEqual(['b,a,c', 'b,c,a'])
  expect(swap(startRequest())).toBe(false)
  respond.shift()!()
  await Bun.sleep(0)
  expect(document.activeElement?.closest('tr')?.dataset.assetId).toBe('a')
})
