#!/usr/bin/env bun
/**
 * Generates `llms.txt` and `llms-full.txt` for the marketing site.
 *
 * Both files follow the llmstxt.org convention and are served from the
 * site root so LLM tooling can find them at the well-known paths:
 *
 *   - /llms.txt       — a curated index: H1, a `>` summary, then H2
 *                       sections linking to each page.
 *   - /llms-full.txt  — the full text of every page concatenated into
 *                       one Markdown document, so a model can ingest the
 *                       whole site without following links.
 *
 * The generator runs AFTER `hugo` against the built `public/` tree and
 * extracts text from the rendered HTML. Reading the published output
 * (rather than the Markdown/data sources) means the file never drifts
 * from what visitors actually see — including the marketing copy that
 * lives in Hugo layouts (home, features, get-started) rather than in
 * `content/*.md`.
 */

import { readdir, readFile, writeFile } from 'node:fs/promises'
import { join, relative } from 'node:path'

import {
  parse,
  NodeType,
  type HTMLElement,
  type Node,
  type TextNode,
} from 'node-html-parser'

const PUBLIC_DIR = join(import.meta.dir, '..', 'public')
const HUGO_CONFIG = join(import.meta.dir, '..', 'hugo.toml')

// Inline elements that, when they appear on their own at block level,
// should be serialized as inline Markdown rather than recursed into
// (keeps e.g. permalink "#" anchors from leaking onto their own line).
const INLINE_TAGS = new Set([
  'a',
  'span',
  'strong',
  'b',
  'em',
  'i',
  'code',
  'small',
  'sup',
  'sub',
  'abbr',
  'time',
  'kbd',
  'mark',
])

// Site chrome and non-prose elements to drop before serializing.
const STRIP_TAGS = new Set([
  'nav',
  'footer',
  'script',
  'style',
  'noscript',
  'svg',
  'head',
  'template',
])

// Preferred ordering for the top of the file; anything not listed
// falls through in alphabetical route order after these.
const ROUTE_ORDER = [
  '/',
  '/features/',
  '/get-started/',
  '/api/',
  '/faq/',
  '/docs/',
]

interface Page {
  route: string
  url: string
  title: string
  description: string
  body: string
}

/** Read `baseURL` and `title` out of hugo.toml without a TOML parser. */
async function readSiteMeta(): Promise<{ baseURL: string; title: string }> {
  const toml = await readFile(HUGO_CONFIG, 'utf8')
  const baseURL = toml.match(/^\s*baseURL\s*=\s*(["'])(.*?)\1/m)?.[2] ?? '/'
  const title = toml.match(/^\s*title\s*=\s*(["'])(.*?)\1/m)?.[2] ?? 'Site'
  return { baseURL: baseURL.replace(/\/$/, ''), title }
}

/** Recursively collect every `index.html` under `public/`. */
async function findHtmlFiles(dir: string): Promise<string[]> {
  const entries = await readdir(dir, { withFileTypes: true })
  const files: string[] = []
  for (const entry of entries) {
    const full = join(dir, entry.name)
    if (entry.isDirectory()) {
      files.push(...(await findHtmlFiles(full)))
    } else if (entry.name === 'index.html') {
      files.push(full)
    }
  }
  return files
}

/** Map a built file path to its site route, e.g. docs/qa/index.html -> /docs/qa/ */
function routeForFile(file: string): string {
  const rel = relative(PUBLIC_DIR, file).replace(/index\.html$/, '')
  return '/' + rel.replace(/\\/g, '/')
}

const collapse = (s: string) => s.replace(/[ \t\r\n]+/g, ' ')

/** Serialize an element's inline children (text, links, emphasis) to Markdown. */
function inline(node: Node): string {
  if (node.nodeType === NodeType.TEXT_NODE)
    return collapse((node as TextNode).text)
  const el = node as HTMLElement
  if (!el.tagName) return ''
  const tag = el.tagName.toLowerCase()
  if (STRIP_TAGS.has(tag)) return ''
  const inner = el.childNodes.map(inline).join('')
  switch (tag) {
    case 'br':
      return '\n'
    case 'a': {
      const href = el.getAttribute('href') ?? ''
      const text = inner.trim()
      if (!text) return ''
      // Drop bare permalink glyphs (e.g. the "#" heading anchors).
      if (/^[#¶§]+$/.test(text)) return ''
      // Keep the visible text but drop the target for in-page anchors.
      if (!href || href.startsWith('#')) return text
      return `[${text}](${href})`
    }
    case 'strong':
    case 'b':
      return inner.trim() ? `**${inner.trim()}**` : ''
    case 'em':
    case 'i':
      return inner.trim() ? `*${inner.trim()}*` : ''
    case 'code':
      return inner.trim() ? `\`${inner.trim()}\`` : ''
    default:
      return inner
  }
}

/**
 * Serialize an element's children that mix inline text with block
 * children (e.g. a list item holding prose plus a code block):
 * consecutive inline nodes are joined into one line, block nodes are
 * emitted as their own entries.
 */
function serializeMixed(el: HTMLElement, out: string[]): void {
  let buf = ''
  const flush = () => {
    const t = buf.trim()
    if (t) out.push(t)
    buf = ''
  }
  for (const child of el.childNodes) {
    if (child.nodeType === NodeType.TEXT_NODE) {
      buf += (child as TextNode).text
      continue
    }
    const c = child as HTMLElement
    const tag = c.tagName?.toLowerCase()
    if (!tag || STRIP_TAGS.has(tag)) continue
    if (INLINE_TAGS.has(tag)) {
      buf += inline(c)
      continue
    }
    flush()
    block(c, out)
  }
  flush()
}

/** Serialize a block-level element tree to a Markdown string. */
function block(node: Node, out: string[]): void {
  if (node.nodeType === NodeType.TEXT_NODE) {
    const text = collapse((node as TextNode).text).trim()
    if (text) out.push(text)
    return
  }
  const el = node as HTMLElement
  if (!el.tagName) return
  const tag = el.tagName.toLowerCase()
  if (STRIP_TAGS.has(tag)) return

  // A standalone inline element at block level: serialize inline so
  // link/emphasis handling (and permalink-glyph stripping) applies.
  if (INLINE_TAGS.has(tag)) {
    const text = inline(el).trim()
    if (text) out.push(text)
    return
  }

  switch (tag) {
    case 'h1':
    case 'h2':
    case 'h3':
    case 'h4':
    case 'h5':
    case 'h6': {
      const level = Number(tag[1])
      const text = inline(el).trim()
      // Down-shift by one so page <h1> becomes "##" under the page's
      // own "#" heading in llms-full.txt.
      if (text) out.push(`${'#'.repeat(Math.min(level + 1, 6))} ${text}`)
      return
    }
    case 'p': {
      const text = inline(el).trim()
      if (text) out.push(text)
      return
    }
    case 'ul':
    case 'ol': {
      const ordered = tag === 'ol'
      const lines: string[] = []
      let i = 1
      for (const li of el.childNodes) {
        if ((li as HTMLElement).tagName?.toLowerCase() !== 'li') continue
        const sub: string[] = []
        serializeMixed(li as HTMLElement, sub)
        const body = sub.join('\n\n').trim()
        if (!body) continue
        const marker = ordered ? `${i++}.` : '-'
        const [first, ...rest] = body.split('\n')
        lines.push(`${marker} ${first}`)
        // Indent any continuation lines (code fences, nested lists)
        // so they stay attached to the bullet.
        for (const r of rest) lines.push(r ? `  ${r}` : '')
      }
      if (lines.length) out.push(lines.join('\n'))
      return
    }
    case 'pre': {
      // node-html-parser treats <pre> as a raw-text element, so its
      // single child holds the inner HTML (Chroma's <code>/<span>
      // wrappers) as an entity-encoded string. Re-parse that raw
      // string and take its text: real code angle brackets are
      // encoded as &lt;/&gt; there, so the wrapper tags strip cleanly
      // while literal "<...>" in the code survives.
      const rawInner = el.childNodes
        .map((c) => (c.nodeType === NodeType.TEXT_NODE ? (c as TextNode).rawText : ''))
        .join('')
      const code = parse(rawInner).textContent.replace(/\n+$/, '')
      if (code.trim()) out.push('```\n' + code + '\n```')
      return
    }
    case 'blockquote': {
      const inner: string[] = []
      for (const child of el.childNodes) block(child, inner)
      const text = inner.join('\n\n').trim()
      if (text)
        out.push(
          text
            .split('\n')
            .map((l) => `> ${l}`)
            .join('\n'),
        )
      return
    }
    case 'table': {
      const rows: string[][] = []
      for (const tr of el.querySelectorAll('tr')) {
        const cells = tr
          .querySelectorAll('th,td')
          // Escape backslashes before pipes so a literal "\" in a cell
          // can't combine with the pipe escape.
          .map((c) =>
            inline(c).trim().replace(/\\/g, '\\\\').replace(/\|/g, '\\|'),
          )
        if (cells.length) rows.push(cells)
      }
      if (rows.length) {
        const md = [`| ${rows[0].join(' | ')} |`]
        md.push(`| ${rows[0].map(() => '---').join(' | ')} |`)
        for (const r of rows.slice(1)) md.push(`| ${r.join(' | ')} |`)
        out.push(md.join('\n'))
      }
      return
    }
    default:
      // Structural container (div, section, header, article, main, …):
      // recurse into children.
      for (const child of el.childNodes) block(child, out)
  }
}

/** Extract a clean Markdown body + metadata from one rendered page. */
function extractPage(html: string, route: string, baseURL: string): Page | null {
  const root = parse(html)

  // Skip Hugo alias/redirect stubs.
  if (root.querySelector('meta[http-equiv=refresh]')) return null

  const title = (root.querySelector('title')?.textContent ?? route).trim()
  const description = (
    root.querySelector('meta[name=description]')?.getAttribute('content') ?? ''
  ).trim()
  const canonical = root
    .querySelector('link[rel=canonical]')
    ?.getAttribute('href')
  const url = canonical ?? baseURL + route

  const body = root.querySelector('body')
  if (!body) return null

  const out: string[] = []
  for (const child of body.childNodes) block(child, out)

  // Collapse runs of blank lines the recursion may have produced.
  const text = out.join('\n\n').replace(/\n{3,}/g, '\n\n').trim()
  return { route, url, title, description, body: text }
}

function orderPages(pages: Page[]): Page[] {
  const rank = (route: string) => {
    const i = ROUTE_ORDER.indexOf(route)
    if (i !== -1) return i
    // Keep docs children grouped right after /docs/ but before unknowns.
    return route.startsWith('/docs/') ? ROUTE_ORDER.length : ROUTE_ORDER.length + 1
  }
  return [...pages].sort(
    (a, b) => rank(a.route) - rank(b.route) || a.route.localeCompare(b.route),
  )
}

function stripHtmlTitleSuffix(title: string): string {
  // Layouts append " | Anthias"; drop it for cleaner section headings.
  return title.replace(/\s*\|\s*Anthias\s*$/, '').trim()
}

async function main() {
  const { baseURL, title } = await readSiteMeta()
  const files = await findHtmlFiles(PUBLIC_DIR)

  const pages: Page[] = []
  for (const file of files) {
    const route = routeForFile(file)
    const html = await readFile(file, 'utf8')
    const page = extractPage(html, route, baseURL)
    if (page && page.body) pages.push(page)
  }

  const ordered = orderPages(pages)
  const summary =
    'Anthias is the world’s most popular open source digital signage ' +
    'software. It turns a Raspberry Pi, x86 PC, or generic 64-bit ARM board ' +
    'into a digital sign that displays images, videos, and web pages, ' +
    'scheduled from a self-hosted web dashboard. No subscriptions, no cloud ' +
    'lock-in.'

  // --- llms.txt (curated index) ---
  const indexLines: string[] = [`# ${title}`, '', `> ${summary}`, '']
  const docs = ordered.filter((p) => p.route.startsWith('/docs/') && p.route !== '/docs/')
  const primary = ordered.filter((p) => !p.route.startsWith('/docs/') || p.route === '/docs/')
  indexLines.push('## Pages', '')
  for (const p of primary) {
    const name = stripHtmlTitleSuffix(p.title)
    indexLines.push(`- [${name}](${p.url})${p.description ? `: ${p.description}` : ''}`)
  }
  if (docs.length) {
    indexLines.push('', '## Documentation', '')
    for (const p of docs) {
      const name = stripHtmlTitleSuffix(p.title)
      indexLines.push(`- [${name}](${p.url})${p.description ? `: ${p.description}` : ''}`)
    }
  }
  indexLines.push('', '## Full text', '')
  indexLines.push(`- [llms-full.txt](${baseURL}/llms-full.txt): the full text of every page on this site, concatenated into one document.`)
  indexLines.push('')

  // --- llms-full.txt (full content) ---
  const fullLines: string[] = [
    `# ${title}`,
    '',
    `> ${summary}`,
    '',
    `This file concatenates the full text of every page on ${baseURL}. Each section is one page, with its source URL.`,
    '',
  ]
  for (const p of ordered) {
    fullLines.push('---', '')
    fullLines.push(`# ${stripHtmlTitleSuffix(p.title)}`, '')
    fullLines.push(`Source: ${p.url}`, '')
    if (p.description) fullLines.push(`> ${p.description}`, '')
    fullLines.push(p.body, '')
  }

  await writeFile(join(PUBLIC_DIR, 'llms.txt'), indexLines.join('\n'))
  await writeFile(join(PUBLIC_DIR, 'llms-full.txt'), fullLines.join('\n'))

  const fullBytes = Buffer.byteLength(fullLines.join('\n'))
  console.log(
    `Wrote llms.txt (${primary.length + docs.length} pages indexed) and ` +
      `llms-full.txt (${ordered.length} pages, ${(fullBytes / 1024).toFixed(1)} KiB).`,
  )
}

main()
