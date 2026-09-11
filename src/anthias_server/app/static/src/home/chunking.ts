// Split a large upload into requests small enough to clear a proxy.
// Anthias accepts a body of any size; a reverse proxy in front of the
// device usually cannot (Cloudflare caps non-Enterprise plans at
// 100 MB), so the bytes arrive in several requests, each carrying the
// Content-Range the server stages by.
//
// The arithmetic sits here, away from the DOM, because it fails
// silently: a wrong final range truncates to the wrong length and an
// off-by-one start leaves a hole reading back as zeros — both an asset
// of exactly the right size that will not play.

export interface Chunk {
  start: number
  // Inclusive, as Content-Range is on the wire — not the exclusive end
  // File.slice() takes.
  end: number
  header: string
}

// The same three numbers resolve_upload_chunk_size_mb clamps to
// server-side, mirrored here so a meta tag that is absent, empty or
// hand-edited cannot produce a chunk size the server would refuse.
// The ceiling keeps a chunk under FILE_UPLOAD_MAX_MEMORY_SIZE (25 MB),
// where Django holds it in RAM rather than spooling it to the SD card
// — what one in-flight upload costs resident on a 512 MB board. The
// floor exists because below it a large file becomes thousands of
// sequential requests; it also keeps this from ever returning 0,
// which would make planChunks yield nothing for a file it had just
// said needed chunking.
const FALLBACK_CHUNK_MB = 16
const MAX_CHUNK_MB = 24
const MIN_CHUNK_MB = 1

export function chunkSizeFromMeta(raw: string | null): number {
  const parsed = Number(raw)
  const mb =
    Number.isFinite(parsed) && parsed > 0
      ? Math.min(Math.max(parsed, MIN_CHUNK_MB), MAX_CHUNK_MB)
      : FALLBACK_CHUNK_MB
  return Math.floor(mb * 1024 * 1024)
}

// Every chunk of a file, in the order they must be sent: strictly in
// sequence, one in flight (see _stage_upload_chunk in views.py for
// why out-of-order discards the upload).
export function planChunks(fileSize: number, chunkSize: number): Chunk[] {
  if (fileSize <= 0 || chunkSize <= 0) return []
  const chunks: Chunk[] = []
  for (let start = 0; start < fileSize; start += chunkSize) {
    const end = Math.min(start + chunkSize, fileSize) - 1
    chunks.push({
      start,
      end,
      header: `bytes ${start}-${end}/${fileSize}`,
    })
  }
  return chunks
}

// One that fits in a single chunk takes the original single-request
// path: the common case stays on well-trodden code, and empty files —
// which have no representable range — are left alone.
export function needsChunking(fileSize: number, chunkSize: number): boolean {
  return fileSize > chunkSize
}
