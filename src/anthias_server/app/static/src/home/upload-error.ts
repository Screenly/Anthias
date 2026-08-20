// Map an asset-upload failure onto the message the operator sees.
//
// The upload path is raw XHR (see uploadOne in home.ts), so htmx never
// sees the response and there is no server toast to replay — every
// non-2xx collapsed into one generic "Upload failed". The status worth
// surfacing is 413: Anthias sets no body limit of its own
// (DATA_UPLOAD_MAX_MEMORY_SIZE is None, and the bundled Caddy sidecar
// sets `request_body { max_size 0 }`), so it always comes from an
// intermediary the operator controls and retrying cannot help.
//
// A single-shot upload answers ENOSPC with 200 plus an HX-Trigger
// toast, which fireToastFromHeader replays. A chunk answers 507 with
// the same text as JSON, which the caller passes through as a
// `server` failure; the 507 branch below is the fallback for when
// that body cannot be read.

// A transport failure carries no HTTP status: XMLHttpRequest reports
// `status === 0` and fires `error` or `abort`. Modelling that as its
// own kind saves the caller inventing a status for "no response".
export type UploadFailure =
  | { kind: 'http'; status: number }
  | { kind: 'network' }
  // The server explained itself in the response body. Its wording is
  // better than anything derivable from a status code, so it wins.
  | { kind: 'server'; message: string }

export function uploadErrorMessage(failure: UploadFailure): string {
  if (failure.kind === 'server') return failure.message

  // Both causes, neither asserted. A proxy enforcing a body limit
  // answers and closes while the browser is still writing, and the
  // browser does not always salvage that response — so a size
  // rejection can arrive here indistinguishable from a dropped
  // connection.
  if (failure.kind === 'network') {
    return (
      'Upload failed mid-transfer — check your connection, or try a ' +
      'smaller file'
    )
  }

  const { status } = failure

  // Name the proxy: the fix lives in the operator's CDN or reverse
  // proxy, not in any Anthias setting they could go looking for.
  if (status === 413) {
    return (
      'File too large — it exceeds the upload size limit of the ' +
      'server or a proxy in front of it'
    )
  }

  // CSRF rejection. An expired session does not land here — authorized
  // answers 302 to /login/, which XHR follows transparently.
  if (status === 403) {
    return 'Upload rejected — reload the page and try again'
  }

  // Before the 5xx branch: a chunked upload reports a full disk as
  // 507, and "check the device logs" would send the operator to the
  // wrong place for something they can act on directly.
  if (status === 507) {
    return 'Not enough space on the device — free some up and try again'
  }

  if (status >= 500) {
    return (
      'The server failed while handling the upload — check the device logs'
    )
  }

  // Everything else reached Anthias and was refused; keep the original
  // wording so unsupported-type reads the way it always has.
  return 'Upload failed — check the file and try again'
}
