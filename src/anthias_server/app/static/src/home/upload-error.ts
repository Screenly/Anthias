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
// No 507 case on purpose — assets_upload answers ENOSPC with 200 plus
// an HX-Trigger toast carrying DISK_FULL_ERROR, which
// fireToastFromHeader already replays. Only the REST API returns 507.

// A transport failure carries no HTTP status: XMLHttpRequest reports
// `status === 0` and fires `error` or `abort`. Modelling that as its
// own kind saves the caller inventing a status for "no response".
export type UploadFailure =
  | { kind: 'http'; status: number }
  | { kind: 'network' }
  // The session expired: the server answered with HX-Redirect and the
  // caller is navigating to the login page. Carries no status because
  // the response was a perfectly ordinary 2xx.
  | { kind: 'auth' }

export function uploadErrorMessage(failure: UploadFailure): string {
  // Usually unseen, since the caller navigates away on this. Worth
  // having anyway: if the navigation is slow the operator gets an
  // explanation rather than a blank moment.
  if (failure.kind === 'auth') {
    return 'Your session expired — sign in again to upload'
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

  if (status >= 500) {
    return (
      'The server failed while handling the upload — check the device logs'
    )
  }

  // Everything else reached Anthias and was refused; keep the original
  // wording so unsupported-type reads the way it always has.
  return 'Upload failed — check the file and try again'
}
