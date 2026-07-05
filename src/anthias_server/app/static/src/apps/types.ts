// A single configurable setting's runtime value. Scalars, or the
// structured values the location-map ({lat,lng}) and array widgets
// produce. Shared by the launch-URL builder and the form renderer.
export type SettingValue =
  | string
  | number
  | boolean
  | null
  | undefined
  | SettingValue[]
  | { [key: string]: SettingValue }

export type SettingValues = Record<string, SettingValue>

// Shapes of the signage-app store index and per-app manifest that the
// Add → Apps tab consumes. These mirror the published contract in the
// app store's docs/app-manifest.md and its JSON Schema
// (static/schemas/signage-app-manifest.schema.json). We type only the
// fields the picker actually reads; unknown keys pass through untouched.

// One JSON Schema property inside `manifest.settings.properties`,
// augmented with the store's `x-*` UI hints (which JSON Schema
// validators ignore). The renderer keys off `x-widget` first, then
// falls back to `type`/`enum`.
export interface SettingSchema {
  type?: 'string' | 'number' | 'integer' | 'boolean' | 'object' | 'array'
  title?: string
  description?: string
  default?: unknown
  enum?: unknown[]
  minimum?: number
  maximum?: number
  properties?: Record<string, SettingSchema>
  items?: SettingSchema
  required?: string[]
  'x-widget'?: string
  'x-enumLabels'?: string[]
  'x-format'?: string
  'x-group'?: string
}

export interface ManifestSettings {
  type: 'object'
  properties: Record<string, SettingSchema>
}

export interface ManifestLaunch {
  baseUrl: string
  template?: string
}

export interface ManifestPlayback {
  pacing?: 'fixed' | 'stepped'
  loops?: boolean
  stepSeconds?: number
  refreshIntervalS?: number
}

export interface AppManifest {
  manifestVersion: string
  id: string
  name: string
  description: string
  summary?: string
  vendor?: string
  tags?: string[]
  icon?: string
  screenshots?: string[]
  homepage?: string
  source?: string
  support?: string
  playback?: ManifestPlayback
  settings?: ManifestSettings
  launch: ManifestLaunch
}

// One entry in the store index (/manifest.json): a pointer to an app's
// self-hosted manifest.
export interface IndexEntry {
  id: string
  manifest: string
}

export interface StoreIndex {
  indexVersion: string
  updated?: string
  apps: IndexEntry[]
}

// A catalog row after the manifest has been fetched and resolved. The
// index pointer plus its loaded manifest.
export interface CatalogApp {
  id: string
  manifestUrl: string
  manifest: AppManifest
}
