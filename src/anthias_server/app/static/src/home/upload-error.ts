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
// toast, which fireToastFromHeader replays; a chunk answers 507 with
// the same text as JSON, passed through as a `server` failure. The
// 507 branch below is the fallback for when that body cannot be read.

// A transport failure carries no HTTP status: XMLHttpRequest reports
// `status === 0` and fires `error` or `abort`. Modelling that as its
// own kind saves the caller inventing a status for "no response".
export type UploadFailure =
  | { kind: 'http'; status: number }
  | { kind: 'network' }
  // A chunked upload whose commit got no response. The asset may
  // exist: the server renames the partial into place before answering,
  // so a lost reply looks exactly like a lost commit.
  | { kind: 'unconfirmed' }
  // The session expired: the server answered with HX-Redirect and the
  // caller is navigating to the login page. Carries no status because
  // the response was a perfectly ordinary 2xx. Deliberately the same
  // shape as the fix in the htmx-auth-redirect PR, so whichever of the
  // two rebases second resolves to the same thing twice rather than
  // silently losing it.
  | { kind: 'auth' }
  // A proxy refused one CHUNK as too large. The file is not the
  // problem — splitting it was supposed to be the fix — so the remedy
  // is a smaller chunk, not a smaller file.
  | { kind: 'chunk-too-large' }
  // The server explained itself in the response body. Its wording is
  // better than anything derivable from a status code, so it wins —
  // the status rides along because the caller still has to know a 409
  // ("it may already be there") from a 507 ("the disk is full").
  | { kind: 'server'; message: string; status: number }

export function uploadErrorMessage(failure: UploadFailure): string {
  // Usually unseen, since the caller navigates away on this. Worth
  // having anyway: if the navigation is slow the operator gets an
  // explanation rather than a blank moment.
  if (failure.kind === 'auth') {
    return 'Your session expired — sign in again to upload'
  }

  if (failure.kind === 'server') return failure.message

  if (failure.kind === 'chunk-too-large') {
    return (
      'Even split up, each part is too large for a proxy in front of ' +
      'the device — lower the upload chunk size'
    )
  }

  // Never "try again": re-uploading something that may have completed
  // is what produces the duplicate.
  if (failure.kind === 'unconfirmed') {
    return (
      'Upload may have finished — check the asset list before ' +
      'uploading it again'
    )
  }

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

  // Before the 5xx branch: a chunk reports a full disk as 507, and
  // "check the device logs" is the wrong place for something the
  // operator can act on directly.
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
