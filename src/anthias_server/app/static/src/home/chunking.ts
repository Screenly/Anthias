// Split a large upload into requests small enough to clear a proxy.
//
// Anthias itself accepts a body of any size, but an operator who puts
// a reverse proxy in front of the device usually cannot: Cloudflare
// caps every non-Enterprise plan at 100 MB. The bytes have to arrive
// in several requests, each carrying the Content-Range the server
// stages by.
//
// The arithmetic lives here, away from the DOM, because the failure it
// guards against is silent: a wrong final range makes the server
// truncate to the wrong length, and an off-by-one start leaves a hole
// that reads back as zeros. Both produce an asset of exactly the right
// size that will not play.

export interface Chunk {
  start: number
  // Inclusive, matching the Content-Range wire format rather than the
  // exclusive end that File.slice() takes.
  end: number
  header: string
}

// Kept under the server's FILE_UPLOAD_MAX_MEMORY_SIZE (25 MB), which
// means Django holds each chunk in RAM rather than spooling it to the
// SD card — so this is what one in-flight upload costs resident on a
// board that may have 512 MB. The server clamps to the same ceiling;
// its own default is 16.
const FALLBACK_CHUNK_MB = 16
const MAX_CHUNK_MB = 24

export function chunkSizeFromMeta(raw: string | null): number {
  const parsed = Number(raw)
  const mb =
    Number.isFinite(parsed) && parsed > 0
      ? Math.min(parsed, MAX_CHUNK_MB)
      : FALLBACK_CHUNK_MB
  return Math.floor(mb * 1024 * 1024)
}

// Every chunk of a file, in the order they must be sent. The server
// tracks a byte count rather than a set of received ranges, so a chunk
// arriving out of order is indistinguishable from a resumed upload
// whose partial has gone, and the whole upload is discarded. Send
// these strictly in sequence, one in flight.
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

// Whether a file should be sent in pieces at all. A file that fits in
// one chunk takes the original single-request path, which keeps the
// common case on well-trodden code and leaves empty files (which have
// no representable range) alone.
export function needsChunking(fileSize: number, chunkSize: number): boolean {
  return fileSize > chunkSize
}
