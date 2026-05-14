#!/usr/bin/env bun
/**
 * Pulls the latest successful marketing-screenshots workflow
 * artifact into assets/images/screenshots/ for local Hugo builds.
 *
 * The CI deploy pipeline (.github/workflows/deploy-website.yaml)
 * does the same thing during production builds; this script exists
 * so contributors can preview the home-page slider with real
 * captures without a custom CI run. Files land gitignored so the
 * fetch is idempotent and never accidentally committed.
 *
 * Requires the `gh` CLI to be installed and authenticated against
 * the upstream repo. Run from the `website/` directory:
 *
 *   bun run screenshots:fetch
 *
 * Pass `--ref <branch>` to pull from a non-master branch's most
 * recent run (handy when developing the capture pipeline itself).
 */
import {
  copyFileSync,
  mkdirSync,
  mkdtempSync,
  readdirSync,
  rmSync,
} from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'

const DEST = 'assets/images/screenshots'

const args = Bun.argv.slice(2)
let branch = 'master'
for (let i = 0; i < args.length; i++) {
  if (args[i] === '--ref' && i + 1 < args.length) {
    branch = args[++i]!
  }
}

async function sh(cmd: string[]): Promise<string> {
  const proc = Bun.spawn(cmd, { stdout: 'pipe', stderr: 'pipe' })
  const stdout = await new Response(proc.stdout).text()
  const stderr = await new Response(proc.stderr).text()
  const status = await proc.exited
  if (status !== 0) {
    throw new Error(
      `command failed (${status}): ${cmd.join(' ')}\n${stderr}`,
    )
  }
  return stdout.trim()
}

const runId = await sh([
  'gh',
  'run',
  'list',
  '--workflow=marketing-screenshots.yaml',
  `--branch=${branch}`,
  '--status=success',
  '--limit=1',
  '--json',
  'databaseId',
  '--jq',
  '.[0].databaseId // ""',
])

if (!runId) {
  console.error(
    `No successful marketing-screenshots run on '${branch}'.\n` +
      `Trigger one with:\n` +
      `  gh workflow run marketing-screenshots.yaml --ref ${branch}\n` +
      `…or pass --ref <branch> to pull from a different branch.`,
  )
  process.exit(1)
}

console.log(`Fetching marketing-screenshots artifact from run ${runId}`)
const tmp = mkdtempSync(join(tmpdir(), 'anthias-screenshots-'))
try {
  await sh([
    'gh',
    'run',
    'download',
    runId,
    '--name',
    'marketing-screenshots',
    '--dir',
    tmp,
  ])
  rmSync(DEST, { recursive: true, force: true })
  mkdirSync(DEST, { recursive: true })
  let copied = 0
  for (const name of readdirSync(tmp)) {
    if (!name.endsWith('.png')) continue
    copyFileSync(join(tmp, name), join(DEST, name))
    copied++
  }
  console.log(`Installed ${copied} screenshots → ${DEST}/`)
} finally {
  rmSync(tmp, { recursive: true, force: true })
}
