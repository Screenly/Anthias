// Fetch the signage-app store catalog entirely in the operator's
// browser: the pointers-only index, then each app's self-hosted
// manifest. Both the index and the manifests send
// Access-Control-Allow-Origin: *, so the device itself never has to
// reach the store — only the browser session does.

import type { AppManifest, CatalogApp, StoreIndex } from './types'

// A manifest must carry at least these to be usable by the picker
// (mirrors the store's own required set). We skip — rather than
// render — anything malformed so one bad app can't break the grid.
function isUsableManifest(m: unknown): m is AppManifest {
  if (!m || typeof m !== 'object') return false
  const manifest = m as Partial<AppManifest>
  return Boolean(
    manifest.manifestVersion &&
      manifest.id &&
      manifest.name &&
      manifest.description &&
      manifest.launch &&
      manifest.launch.baseUrl,
  )
}

async function fetchJson(url: string, signal?: AbortSignal): Promise<unknown> {
  const res = await fetch(url, { signal, credentials: 'omit' })
  if (!res.ok) {
    throw new Error(`Fetch failed (${res.status}) for ${url}`)
  }
  return res.json()
}

// Fetch and validate a single app manifest by URL (used by the edit
// modal to reopen an installed app's config form). Throws on a bad
// response or a manifest missing its required fields.
export async function fetchManifest(
  url: string,
  signal?: AbortSignal,
): Promise<AppManifest> {
  const manifest = await fetchJson(url, signal)
  if (!isUsableManifest(manifest)) {
    throw new Error('Manifest is missing required fields')
  }
  return manifest
}

// Load the index and resolve every app's manifest in parallel. A
// manifest that fails to load or is malformed is dropped (logged) so a
// single dead origin degrades gracefully to a shorter list. Resolves
// in index order.
export async function loadCatalog(
  indexUrl: string,
  signal?: AbortSignal,
): Promise<CatalogApp[]> {
  const index = (await fetchJson(indexUrl, signal)) as StoreIndex
  if (!index || !Array.isArray(index.apps)) {
    throw new Error('Store index is missing an apps list')
  }

  const settled = await Promise.allSettled(
    index.apps.map(async (entry): Promise<CatalogApp> => {
      const manifest = await fetchJson(entry.manifest, signal)
      if (!isUsableManifest(manifest)) {
        throw new Error(`Manifest for ${entry.id} is missing required fields`)
      }
      return { id: entry.id, manifestUrl: entry.manifest, manifest }
    }),
  )

  const apps: CatalogApp[] = []
  settled.forEach((result, i) => {
    if (result.status === 'fulfilled') {
      apps.push(result.value)
    } else {
      // Don't spam the console with abort noise when the modal closes
      // mid-load; those reject with an AbortError we simply ignore.
      const reason = result.reason
      if (!(reason instanceof DOMException && reason.name === 'AbortError')) {
        console.warn(
          `Skipping app "${index.apps[i]?.id}": ${String(reason)}`,
        )
      }
    }
  })
  return apps
}
