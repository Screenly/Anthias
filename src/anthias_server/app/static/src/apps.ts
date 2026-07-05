// Add → Apps tab: browse the signage-app store catalog, configure an
// app from its manifest, and install it as a webpage asset.
//
// Exposes `appsTab()` as an Alpine component factory (registered on
// window by home.ts). The catalog fetch, manifest-form rendering and
// launch-URL building live in ./apps/*; this file is the reactive glue
// and owns nothing that isn't UI state. The actual create request is an
// htmx <form> in _asset_modal.html (hidden inputs bound to this
// component), so app installs reuse the exact same #asset-table swap +
// asset-saved contract as the "From URL" tab.

import { fetchManifest, loadCatalog } from './apps/catalog'
import { buildLaunchUrl } from './apps/launch-url'
import { renderManifestForm, teardownHost } from './apps/manifest-form'
import type { CatalogApp, SettingValue } from './apps/types'

type Phase = 'loading' | 'ready' | 'error' | 'config'

export interface AppsTabData {
  phase: Phase
  apps: CatalogApp[]
  error: string
  selected: CatalogApp | null
  // Bound to hidden form inputs (see the install <form>).
  assetName: string
  launchUrl: string
  valuesJson: string
  loaded: boolean
  init(): void
  load(): Promise<void>
  retry(): void
  select(app: CatalogApp): void
  back(): void
  // The app currently being configured has a settings schema.
  readonly hasConfig: boolean
  // Fields the install form needs alongside the built launch URL.
  readonly appId: string
  readonly manifestUrl: string
  readonly manifestVersion: string
  readonly refreshIntervalS: string
}

function indexUrl(): string {
  const el = document.querySelector<HTMLMetaElement>(
    'meta[name="anthias-app-store-index"]',
  )
  return el?.content ?? ''
}

// Leaflet's control container measures itself at mount; when a config
// panel opens we let Alpine insert the host, then render into it on the
// next frame.
type WithRefs = AppsTabData & { $refs: Record<string, HTMLElement> }

export function appsTab(): AppsTabData {
  let abort: AbortController | null = null

  return {
    phase: 'loading',
    apps: [],
    error: '',
    selected: null,
    assetName: '',
    launchUrl: '',
    valuesJson: '{}',
    loaded: false,

    init(this: AppsTabData) {
      // Lazy: nothing here. The Apps pane triggers load() the first
      // time it becomes visible via x-effect="if (tab === 'apps')
      // load()" in the template, so the catalog is only fetched when
      // the operator actually opens the tab.
    },

    async load(this: AppsTabData) {
      if (this.loaded && this.phase !== 'error') return
      const url = indexUrl()
      if (!url) {
        this.phase = 'error'
        this.error = 'No app store configured.'
        return
      }
      this.phase = 'loading'
      this.error = ''
      abort?.abort()
      abort = new AbortController()
      try {
        this.apps = await loadCatalog(url, abort.signal)
        this.loaded = true
        if (!this.apps.length) {
          this.phase = 'error'
          this.error = 'No apps are available right now.'
          return
        }
        this.phase = 'ready'
      } catch (e) {
        if (e instanceof DOMException && e.name === 'AbortError') return
        this.phase = 'error'
        this.error =
          "Couldn't reach the app store. Check your network connection " +
          'and try again.'
      }
    },

    retry(this: AppsTabData) {
      this.loaded = false
      void this.load()
    },

    select(this: WithRefs, app: CatalogApp) {
      this.selected = app
      this.assetName = app.manifest.name
      this.phase = 'config'
      const launch = app.manifest.launch
      const properties = app.manifest.settings?.properties
      const hasProps = !!(properties && Object.keys(properties).length)
      // A no-settings app installs at its base URL with no values.
      this.launchUrl = launch.baseUrl
      this.valuesJson = '{}'
      // Always clear the config host (Alpine keeps the same element
      // across selections, so a prior app's rendered fields would
      // otherwise linger — visibly wrong for a no-settings app). Render
      // the new form only when there are settings.
      requestAnimationFrame(() => {
        const host = this.$refs.configHost
        if (!host) return
        // Tear down a prior app's controls (incl. any Leaflet map) even
        // when the new app has no settings — a bare clear would leak.
        teardownHost(host)
        if (!hasProps || !properties) return
        renderManifestForm(host, properties, {}, (values, defaults) => {
          this.launchUrl = buildLaunchUrl(
            launch.baseUrl,
            launch.template ?? '',
            values,
            defaults,
          )
          this.valuesJson = JSON.stringify(pruneEmpty(values, defaults))
        })
      })
    },

    back(this: AppsTabData) {
      this.selected = null
      this.phase = this.loaded ? 'ready' : 'loading'
      this.launchUrl = ''
      this.valuesJson = '{}'
    },

    get hasConfig(): boolean {
      const props = this.selected?.manifest.settings?.properties
      return Boolean(props && Object.keys(props).length)
    },
    get appId(): string {
      return this.selected?.manifest.id ?? ''
    },
    get manifestUrl(): string {
      return this.selected?.manifestUrl ?? ''
    },
    get manifestVersion(): string {
      return this.selected?.manifest.manifestVersion ?? ''
    },
    get refreshIntervalS(): string {
      const s = this.selected?.manifest.playback?.refreshIntervalS
      return s ? String(s) : ''
    },
  }
}

// Minimal shape of the edit modal's asset blob that appEdit reads.
interface AppAssetMeta {
  app?: {
    id?: string
    manifest_url?: string
    values?: Record<string, SettingValue>
  }
}
export interface EditAsset {
  uri?: string | null
  metadata?: AppAssetMeta | null
}

export interface AppEditData {
  phase: 'loading' | 'ready' | 'error'
  error: string
  // Bound to the edit form's hidden app_uri / app_values inputs.
  appUri: string
  appValuesJson: string
  init(): void
}

// Edit-side counterpart of appsTab: reopen an installed app's config
// form seeded from its saved metadata.app.values, and rebuild the
// launch URL + values as the operator changes them. The edit <form>
// posts appUri/appValuesJson back to assets_update. Rendered only for
// assets that carry metadata.app (see the edit modal template).
export function appEdit(asset: EditAsset): AppEditData {
  let abort: AbortController | null = null

  return {
    phase: 'loading',
    error: '',
    appUri: asset?.uri ?? '',
    appValuesJson: JSON.stringify(asset?.metadata?.app?.values ?? {}),

    init(this: WithEditRefs) {
      const app = asset?.metadata?.app
      const manifestUrl = app?.manifest_url
      if (!manifestUrl) {
        this.phase = 'error'
        this.error = 'This app has no manifest reference.'
        return
      }
      abort = new AbortController()
      fetchManifest(manifestUrl, abort.signal)
        .then((manifest) => {
          const launch = manifest.launch
          const properties = manifest.settings?.properties
          if (!properties || !Object.keys(properties).length) {
            // A no-settings app has nothing to reconfigure; keep the
            // saved URL and show a note.
            this.phase = 'ready'
            return
          }
          this.phase = 'ready'
          requestAnimationFrame(() => {
            const host = this.$refs.appEditHost
            if (!host) return
            const saved = app?.values ?? {}
            renderManifestForm(
              host,
              properties,
              saved,
              (values, defaults) => {
                this.appUri = buildLaunchUrl(
                  launch.baseUrl,
                  launch.template ?? '',
                  values,
                  defaults,
                )
                this.appValuesJson = JSON.stringify(
                  pruneEmpty(values, defaults),
                )
              },
            )
          })
        })
        .catch((e) => {
          if (e instanceof DOMException && e.name === 'AbortError') return
          this.phase = 'error'
          this.error =
            "Couldn't load this app's settings. Check your network " +
            'connection and reopen.'
        })
    },
  }
}

type WithEditRefs = AppEditData & { $refs: Record<string, HTMLElement> }

// Keep only the values the operator actually set (differ from their
// schema default / non-empty), matching what buildLaunchUrl emits, so
// metadata.app.values round-trips 1:1 with the launch URL rather than
// carrying every untouched default.
function pruneEmpty(
  values: Record<string, SettingValue>,
  defaults: Record<string, SettingValue>,
): Record<string, SettingValue> {
  const out: Record<string, SettingValue> = {}
  for (const [key, value] of Object.entries(values)) {
    if (value === undefined || value === null || value === '' || value === false) {
      continue
    }
    if (JSON.stringify(value) === JSON.stringify(defaults[key])) continue
    out[key] = value
  }
  return out
}
