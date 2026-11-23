// Docs search (layouts/partials/docs-search.html). The index is built by
// Hugo (layouts/docs/list.searchindex.json): one record per section of a
// docs page, plus the FAQ answers. It is fetched the first time the box
// gets focus, so pages that never use search never download it. Loaded
// with `defer` from baseof.html on /docs/ pages only.

interface Entry {
  p: string
  h: string
  u: string
  x: string
}

interface Doc extends Entry {
  title: string
  heading: string
  text: string
}

interface Hit {
  doc: Doc
  score: number
  matched: number
}

const MAX_RESULTS = 8
const SNIPPET_LENGTH = 160

const root = document.querySelector<HTMLElement>('[data-docs-search]')
const input = root?.querySelector<HTMLInputElement>('input')
const list = root?.querySelector<HTMLUListElement>('[role="listbox"]')
const statusEl = root?.querySelector<HTMLElement>('[data-search-status]')

// Lower-case, drop accents and turn punctuation into spaces, so "Wi-Fi"
// matches "wifi" and "wi fi". Every string is folded the same way, so
// offsets in the folded text line up with the original only for the
// snippet's display; matching never needs them to.
function fold(s: string): string {
  return s
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
}

// Hyphenated and contracted words also go in whole ("Wi-Fi" as "wifi"),
// so a search finds them however the visitor spells them.
function joined(s: string): string {
  const words = s.match(/[\p{L}\p{N}]+(?:[-'’][\p{L}\p{N}]+)+/gu) ?? []
  return words.map((w) => fold(w).replace(/ /g, '')).join(' ')
}

function index(s: string): string {
  return `${fold(s)} ${joined(s)}`.replace(/ +/g, ' ').trim()
}

function terms(query: string): string[] {
  return fold(query).split(' ').filter(Boolean)
}

// How many times `term` starts a word in `text` (both folded, so words
// are separated by single spaces).
function wordStarts(text: string, term: string): number {
  let n = 0
  let i = text.indexOf(term)
  while (i !== -1) {
    if (i === 0 || text[i - 1] === ' ') n++
    i = text.indexOf(term, i + term.length)
  }
  return n
}

// Every query word that appears in the record, at the start of a word,
// adds to its score: most in the heading, then the page title, then the
// text. A record that has the whole query as a phrase ranks higher still.
function score(doc: Doc, words: string[], phrase: string): Hit {
  let total = 0
  let matched = 0
  for (const w of words) {
    const inHeading = wordStarts(doc.heading, w)
    const inTitle = wordStarts(doc.title, w)
    const inText = wordStarts(doc.text, w)
    if (!inHeading && !inTitle && !inText) continue
    matched++
    total += (inHeading ? 12 : 0) + (inTitle ? 5 : 0) + Math.min(inText, 6)
  }
  if (words.length > 1) {
    if (doc.heading.includes(phrase)) total += 20
    if (doc.text.includes(phrase)) total += 8
  }
  return { doc, score: total, matched }
}

// Records that have every query word come first. When none does, show
// the ones that have the most of them rather than nothing at all.
function search(docs: Doc[], query: string): Hit[] {
  const words = terms(query)
  if (!words.length) return []
  const phrase = words.join(' ')
  const hits = docs.map((d) => score(d, words, phrase)).filter((h) => h.matched)
  const best = Math.max(0, ...hits.map((h) => h.matched))
  return hits
    .filter((h) => h.matched === best)
    .sort((a, b) => b.score - a.score)
    .slice(0, MAX_RESULTS)
}

// `text` with accents dropped and in lower case, one character for one,
// so a match in it is at the same offsets in `text`. Characters that
// would change length are left as they are.
function plain(text: string): string {
  return Array.from(text, (c) => {
    const f = c
      .normalize('NFD')
      .replace(/[\u0300-\u036f]/g, '')
      .toLowerCase()
    return f.length === c.length ? f : c
  }).join('')
}

// A regex that finds any query word at the start of a word, for the
// snippet and the highlights. It runs on plain(text) and lets a hyphen
// or apostrophe sit between letters, so "wifi" marks "Wi-Fi" the same
// way the search matched it.
function matcher(words: string[]): RegExp {
  const patterns = [...words]
    .sort((a, b) => b.length - a.length)
    .map((w) => Array.from(w).join("[-'’]?"))
  return new RegExp(`(?<![\\p{L}\\p{N}])(?:${patterns.join('|')})`, 'gu')
}

function snippet(text: string, re: RegExp): string {
  re.lastIndex = 0
  const first = re.exec(plain(text))
  if (!first || text.length <= SNIPPET_LENGTH) {
    return text.length > SNIPPET_LENGTH
      ? `${text.slice(0, SNIPPET_LENGTH).trimEnd()}…`
      : text
  }
  let start = Math.max(0, first.index - 40)
  const space = text.lastIndexOf(' ', start)
  if (start > 0 && space !== -1 && start - space < 20) start = space + 1
  const end = Math.min(text.length, start + SNIPPET_LENGTH)
  return `${start > 0 ? '…' : ''}${text.slice(start, end).trim()}${end < text.length ? '…' : ''}`
}

// Append `text` to `el` with the matches wrapped in <mark>. Built from
// text nodes, never innerHTML, so nothing in the index is parsed as HTML.
function appendHighlighted(el: HTMLElement, text: string, re: RegExp): void {
  re.lastIndex = 0
  let last = 0
  for (const m of plain(text).matchAll(re)) {
    const i = m.index ?? 0
    if (i > last) el.append(text.slice(last, i))
    const mark = document.createElement('mark')
    mark.textContent = text.slice(i, i + m[0].length)
    el.append(mark)
    last = i + m[0].length
  }
  if (last < text.length) el.append(text.slice(last))
}

if (root && input && list) {
  let docs: Promise<Doc[]> | null = null
  let active = -1

  const load = (): Promise<Doc[]> => {
    docs ??= fetch(root.dataset.index ?? '')
      .then((r) => {
        if (!r.ok) throw new Error(`${r.status} ${r.statusText}`)
        return r.json() as Promise<Entry[]>
      })
      .then((entries) =>
        entries.map((e) => ({
          ...e,
          title: index(e.p),
          heading: index(e.h),
          text: index(e.x),
        })),
      )
      .catch((err: Error) => {
        docs = null
        throw err
      })
    return docs
  }

  const options = (): HTMLAnchorElement[] =>
    Array.from(list.querySelectorAll<HTMLAnchorElement>('[role="option"]'))

  const setOpen = (open: boolean): void => {
    list.classList.toggle('hidden', !open)
    input.setAttribute('aria-expanded', String(open))
    if (!open) {
      active = -1
      input.removeAttribute('aria-activedescendant')
    }
  }

  const setActive = (i: number): void => {
    const opts = options()
    if (!opts.length) return
    active = (i + opts.length) % opts.length
    opts.forEach((o, n) =>
      o.setAttribute('aria-selected', String(n === active)),
    )
    input.setAttribute('aria-activedescendant', opts[active].id)
    opts[active].scrollIntoView({ block: 'nearest' })
  }

  const message = (text: string): void => {
    const li = document.createElement('li')
    li.className = 'docs-search-empty'
    li.textContent = text
    list.replaceChildren(li)
  }

  const render = (hits: Hit[], query: string): void => {
    active = -1
    input.removeAttribute('aria-activedescendant')
    if (!hits.length) {
      message(`No results for “${query.trim()}”`)
      if (statusEl) statusEl.textContent = 'No results'
      setOpen(true)
      return
    }
    const re = matcher(terms(query))
    const items = hits.map(({ doc }, n) => {
      const li = document.createElement('li')
      const a = document.createElement('a')
      a.id = `docs-search-option-${n}`
      a.href = doc.u
      a.setAttribute('role', 'option')
      a.setAttribute('aria-selected', 'false')
      a.tabIndex = -1

      const crumb = document.createElement('span')
      crumb.className = 'docs-search-crumb'
      crumb.textContent = doc.p
      const title = document.createElement('span')
      title.className = 'docs-search-title'
      appendHighlighted(title, doc.h || doc.p, re)
      a.append(crumb, title)

      if (doc.x) {
        const text = document.createElement('span')
        text.className = 'docs-search-text'
        appendHighlighted(text, snippet(doc.x, re), re)
        a.append(text)
      }
      li.append(a)
      return li
    })
    list.replaceChildren(...items)
    if (statusEl) {
      statusEl.textContent = `${hits.length} result${hits.length === 1 ? '' : 's'}`
    }
    setOpen(true)
  }

  const update = async (): Promise<void> => {
    const query = input.value
    if (!terms(query).length) {
      list.replaceChildren()
      setOpen(false)
      return
    }
    try {
      const hits = search(await load(), query)
      // A newer keystroke may have landed while the index loaded.
      if (input.value === query) render(hits, query)
    } catch {
      message('Search is unavailable right now. Please try again.')
      setOpen(true)
    }
  }

  input.addEventListener('focus', () => {
    load().catch(() => undefined)
    if (input.value.trim()) void update()
  })
  input.addEventListener('input', () => void update())

  input.addEventListener('keydown', (ev) => {
    const open = !list.classList.contains('hidden')
    if (ev.key === 'ArrowDown' || ev.key === 'ArrowUp') {
      if (!open) return
      ev.preventDefault()
      // From no selection, Down picks the first result and Up the last.
      if (ev.key === 'ArrowDown') setActive(active + 1)
      else setActive(active === -1 ? -1 : active - 1)
    } else if (ev.key === 'Enter') {
      const target = options()[active === -1 ? 0 : active]
      if (open && target) {
        ev.preventDefault()
        target.click()
      }
    } else if (ev.key === 'Escape') {
      if (open) {
        ev.preventDefault()
        setOpen(false)
      } else if (input.value) {
        input.value = ''
      } else {
        input.blur()
      }
    }
  })

  // Close when focus or a click lands outside the search box. Clicking a
  // result follows its link; same-page anchors also close the list.
  document.addEventListener('pointerdown', (ev) => {
    if (!root.contains(ev.target as Node)) setOpen(false)
  })
  root.addEventListener('focusout', (ev) => {
    if (!root.contains(ev.relatedTarget as Node | null)) setOpen(false)
  })
  list.addEventListener('click', () => {
    setOpen(false)
    input.blur()
  })

  // "/" or Ctrl/Cmd+K jumps to the box from anywhere on the page, unless
  // the visitor is already typing somewhere.
  document.addEventListener('keydown', (ev) => {
    const target = ev.target as HTMLElement
    const typing =
      target.isContentEditable ||
      ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)
    const slash = ev.key === '/' && !typing && !ev.ctrlKey && !ev.metaKey
    const k = ev.key.toLowerCase() === 'k' && (ev.ctrlKey || ev.metaKey)
    if (!slash && !k) return
    ev.preventDefault()
    input.focus()
    input.select()
  })
}
