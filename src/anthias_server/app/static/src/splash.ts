// Splash-page client bundle. Standalone — does NOT pull vendor.js
// (htmx/Alpine/flatpickr) since the splash is a static page rendered
// by the device's webview while no asset is scheduled. Keeping the
// bundle small matters: the splash is the first paint after boot on
// constrained devices (Pi 1 / Qt5 webview).

import QRCode from 'qrcode'

interface IpResponse {
  ip_addresses?: string[]
}

interface SplashWindow extends Window {
  __splashIpsUrl?: string
}

const w = window as SplashWindow

const detecting = document.getElementById('splash-status-detecting')
const ready = document.getElementById('splash-status-ready')
const list = document.getElementById('splash-ip-list')
const qrSlot = document.getElementById('splash-qr')
const qrCard = document.getElementById('splash-qr-card')

// 120s hard cap — see splash-page.html for the rationale.
const startedAt = Date.now()
const maxPollMs = 120 * 1000
let attempts = 0
let lastSig = ''

function svgFor(url: string): Promise<string> {
  // Browser build of `qrcode` only emits SVG from `toString`. Margin 1
  // (default is 4) keeps the symbol tight against the card; level 'M'
  // is enough for a short LAN URL and stays low-density so the symbol
  // remains scannable on a 720p TV from across a room.
  return QRCode.toString(url, {
    errorCorrectionLevel: 'M',
    margin: 1,
    type: 'svg',
  })
}

async function render(ips: string[]): Promise<void> {
  const sig = ips.join('|')
  if (sig === lastSig) return
  lastSig = sig

  if (!list) return

  if (!ips.length) {
    if (detecting) detecting.hidden = false
    if (ready) ready.hidden = true
    list.innerHTML = ''
    if (qrCard) qrCard.hidden = true
    if (qrSlot) qrSlot.innerHTML = ''
    return
  }

  if (detecting) detecting.hidden = true
  if (ready) ready.hidden = false

  list.innerHTML = ''
  ips.forEach((ip) => {
    const a = document.createElement('a')
    a.href = ip
    a.textContent = ip
    a.className = 'splash-ip-pill'
    list.appendChild(a)
  })

  if (qrSlot && qrCard) {
    try {
      qrSlot.innerHTML = await svgFor(ips[0])
      qrCard.hidden = false
    } catch {
      qrSlot.innerHTML = ''
      qrCard.hidden = true
    }
  }
}

function poll(): void {
  const url = w.__splashIpsUrl
  if (!url) return
  fetch(url, { cache: 'no-store' })
    .then((r) => (r.ok ? r.json() : ({ ip_addresses: [] } as IpResponse)))
    .then((data: IpResponse) => render(data.ip_addresses || []))
    .catch(() => {
      /* keep polling on transient errors */
    })
    .finally(() => {
      attempts++
      if (Date.now() - startedAt >= maxPollMs) return
      // 2s for the first ~30s, then back off to 5s. Host bus usually
      // answers well before 30s; the slow tier is for long-tail
      // recoveries during the rest of the splash's ~60s window.
      window.setTimeout(poll, attempts < 15 ? 2000 : 5000)
    })
}

poll()
