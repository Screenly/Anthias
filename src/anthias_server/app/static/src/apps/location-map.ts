// Reusable location-picker map, built on Leaflet + OpenStreetMap
// tiles.
//
// Anthias can't use the app store's Google-Maps picker: its API key is
// referrer-locked to *.srly.io, so it would be rejected on a device's
// own origin. Leaflet + OSM needs no key and works from any origin, and
// the library + its CSS are bundled locally (no CDN). Tiles are fetched
// from OSM in the operator's browser — the same "browser has internet,
// the device need not" model the whole Apps tab relies on.
//
// The UI is a fixed centre pin over a draggable map (you drag the map
// under the pin to choose a point), matching the app store's control:
// no Leaflet marker is placed, so none of Leaflet's marker/​layers
// images are referenced and their absence is harmless.

import L from 'leaflet'

const DEFAULT_CENTER: L.LatLngTuple = [51.5287718, -0.2417001]
const DEFAULT_ZOOM = 11
const DEFAULT_PRECISION = 4

export interface LocationMapOptions {
  // Coordinates are reported as numbers (not toFixed strings) so a
  // {lat,lng} value compares cleanly against a numeric schema default
  // in buildLaunchUrl — a string "51.5" would never equal the number
  // 51.5 and would leak into the URL as a phantom change.
  onChange?: (coords: { lat: number; lng: number }) => void
  precision?: number
}

// Swap a mount over to a quiet fallback when Leaflet can't initialise
// (e.g. tiles unreachable). The launch URL still works without a
// location — the app auto-detects by IP.
function showUnavailable(mount: HTMLElement): void {
  mount.classList.add('app-cfg-map--unavailable')
  mount.textContent =
    'Map unavailable — the app will auto-detect a location.'
}

const noop = (): void => {}

// Returns a teardown that removes the map and disconnects its
// ResizeObserver; the form renderer calls it before re-rendering so
// repeated open/select cycles don't leak detached Leaflet maps.
export function initLocationMap(
  mount: HTMLElement,
  options: LocationMapOptions = {},
): () => void {
  const { onChange, precision = DEFAULT_PRECISION } = options

  // Host markup may seed the starting view via data-attributes (add
  // mode: schema default; edit mode: the saved pin). A seeded map opens
  // with a location already chosen; an unseeded one stays *unset* until
  // the operator drags, so a no-default app auto-detects by IP.
  const hasSeed = mount.dataset.lat !== undefined
  const startCenter: L.LatLngTuple = [
    Number(mount.dataset.lat ?? DEFAULT_CENTER[0]),
    Number(mount.dataset.lng ?? DEFAULT_CENTER[1]),
  ]
  const startZoom = Number(mount.dataset.zoom ?? DEFAULT_ZOOM)

  try {
    const canvas = document.createElement('div')
    canvas.className = 'app-cfg-map__canvas'
    const pin = document.createElement('span')
    pin.className = 'app-cfg-map__pin'
    pin.setAttribute('aria-hidden', 'true')
    const readout = document.createElement('div')
    readout.className = 'app-cfg-map__readout'
    // Until the operator picks a point (or the map was seeded), the
    // readout is a hint rather than coordinates — showing the default
    // centre's numbers would read as "a location is set" when it isn't.
    const hint = document.createElement('div')
    hint.className = 'app-cfg-map__hint'
    hint.textContent = 'Drag to set location'
    mount.append(canvas, pin, readout, hint)

    const map = L.map(canvas, {
      center: startCenter,
      zoom: startZoom,
      zoomControl: true,
      attributionControl: true,
      // Scroll-wheel zoom would hijack the modal's scroll; keep it to
      // the +/- control and double-click.
      scrollWheelZoom: false,
    })

    L.tileLayer('https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png', {
      maxZoom: 18,
      attribution:
        '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>',
    }).addTo(map)

    // Only after a real user gesture is the location considered
    // "chosen". Programmatic moves (the initial view, invalidateSize
    // re-layouts) fire `moveend` too, so gating the emit on this flag
    // keeps a no-default app's location unset until the operator acts.
    let touched = hasSeed
    const markTouched = (): void => {
      if (touched) return
      touched = true
      mount.classList.add('is-touched')
    }
    if (hasSeed) mount.classList.add('is-touched')

    const renderCoords = (): void => {
      const c = map.getCenter()
      readout.textContent = `${c.lat.toFixed(precision)}, ${c.lng.toFixed(precision)}`
    }
    const round = (n: number): number => Number(n.toFixed(precision))
    const emit = (): void => {
      const c = map.getCenter()
      onChange?.({ lat: round(c.lat), lng: round(c.lng) })
    }

    map.on('dragstart', markTouched)
    // A double-click zoom recentres on the clicked point, so it also
    // sets the location.
    map.on('dblclick', markTouched)
    map.on('move', renderCoords)
    map.on('moveend', () => {
      if (touched) emit()
    })
    renderCoords()
    // Seeded (edit) maps report their saved value straight away so the
    // form's launch URL reflects it without waiting for a drag.
    if (hasSeed) emit()

    // The map is created inside a modal panel that animates in from
    // 0-height, so at first paint the canvas has no size and Leaflet
    // only loads a couple of tiles for a tiny viewport (the rest of the
    // frame stays blank). A ResizeObserver recomputes + reloads tiles
    // whenever the canvas actually gets its size — robust to the modal
    // transition and to the panel scrolling into view — where a single
    // rAF invalidateSize fired too early.
    let ro: ResizeObserver | null = null
    if (typeof ResizeObserver !== 'undefined') {
      ro = new ResizeObserver(() => map.invalidateSize())
      ro.observe(canvas)
    } else {
      requestAnimationFrame(() => map.invalidateSize())
      setTimeout(() => map.invalidateSize(), 300)
    }

    return () => {
      ro?.disconnect()
      map.remove()
    }
  } catch {
    showUnavailable(mount)
    return noop
  }
}
