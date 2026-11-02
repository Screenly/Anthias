// Page tools (layouts/partials/page-tools.html): "Copy as Markdown"
// fetches the page's index.md, written next to it at build time by
// scripts/generate-llms.ts, and puts it on the clipboard. Loaded with
// `defer` from baseof.html; it does nothing until a button is clicked.

const copyStatus = document.querySelector<HTMLElement>('[data-copy-status]')

// Copy a promise of a string. ClipboardItem with a promise keeps the
// click's user activation in Safari while the Markdown is fetched.
async function copy(text: Promise<string>): Promise<void> {
  if ('ClipboardItem' in window && navigator.clipboard.write) {
    const blob = text.then((t) => new Blob([t], { type: 'text/plain' }))
    await navigator.clipboard.write([new ClipboardItem({ 'text/plain': blob })])
    return
  }
  await navigator.clipboard.writeText(await text)
}

function showResult(button: HTMLElement, ok: boolean): void {
  const message = ok ? 'Copied' : 'Could not copy'
  if (copyStatus) copyStatus.textContent = ok ? 'Markdown copied' : message
  const label = button.querySelector('span')
  if (!label) return
  const before = label.textContent
  label.textContent = message
  setTimeout(() => (label.textContent = before), 1600)
}

document.addEventListener('click', async (ev) => {
  const button = (ev.target as Element).closest<HTMLElement>('[data-copy-url]')
  if (!button?.dataset.copyUrl) return
  const text = fetch(button.dataset.copyUrl).then((r) => {
    if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
    return r.text()
  })
  try {
    await copy(text)
    showResult(button, true)
  } catch {
    showResult(button, false)
  }
})
