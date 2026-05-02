#!/usr/bin/env bun
/**
 * Materializes Plus Jakarta Sans from the @fontsource/plus-jakarta-sans
 * package so the font is self-hosted (no third-party CDN, satisfies
 * SonarCloud Web:S5725 SRI hotspot).
 *
 * - Copies woff2 files for latin + latin-ext × {400,500,600,700,800}
 *   into static/fonts/ so Hugo serves them directly at /fonts/...
 *   (anything under static/ is published 1:1 to the site root).
 * - Emits a combined plus-jakarta-sans.css under assets/fonts/ with
 *   the urls rewritten to /fonts/... (absolute, so Lightning CSS
 *   doesn't relativize during the Tailwind build) and the woff
 *   fallback stripped (every browser we care about speaks woff2).
 *   The CSS lives under assets/ because it's @imported by main.css
 *   and consumed at Tailwind build time, never served directly.
 *
 * Run automatically before css:build / css:watch via package.json.
 */
import { copyFileSync, mkdirSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

const WEIGHTS = [400, 500, 600, 700, 800] as const
const SUBSETS = new Set(['latin', 'latin-ext'])
const PKG = 'node_modules/@fontsource/plus-jakarta-sans'
const FONTS_OUT = 'static/fonts'
const CSS_OUT = 'assets/fonts'

mkdirSync(FONTS_OUT, { recursive: true })
mkdirSync(CSS_OUT, { recursive: true })

const css: string[] = [
  '/* Plus Jakarta Sans — installed from @fontsource/plus-jakarta-sans */',
  '/* Regenerate via: bun run fonts:install */',
  '',
]

let copied = 0

for (const weight of WEIGHTS) {
  const source = readFileSync(join(PKG, `${weight}.css`), 'utf-8')
  // Each @font-face is preceded by /* plus-jakarta-sans-<subset>-<weight>-normal */
  const blocks = source.split(/(?=\/\* plus-jakarta-sans-)/)
  for (const block of blocks) {
    const header = block.match(/\/\* plus-jakarta-sans-(\S+?)-\d+-normal \*\//)
    if (!header) continue
    const subset = header[1]
    if (!SUBSETS.has(subset)) continue

    const filename = `plus-jakarta-sans-${subset}-${weight}-normal.woff2`
    copyFileSync(join(PKG, 'files', filename), join(FONTS_OUT, filename))
    copied++

    const rewritten = block
      .replace(/url\(\.\/files\/[^)]+\.woff2\)/, `url('/fonts/${filename}')`)
      .replace(/, url\(\.\/files\/[^)]+\.woff\) format\('woff'\)/, '')
      .trimEnd()
    css.push(rewritten, '')
  }
}

writeFileSync(join(CSS_OUT, 'plus-jakarta-sans.css'), css.join('\n'))
console.log(
  `installed Plus Jakarta Sans: ${copied} woff2 → ${FONTS_OUT}/, css → ${CSS_OUT}/plus-jakarta-sans.css`,
)
