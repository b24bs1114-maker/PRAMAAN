/**
 * Runner for tests/contract.test.tsx.
 *
 * The contract tests import the real client, which is TypeScript with
 * extensionless imports and `import.meta.env` — neither of which Node resolves
 * on its own. So this bundles the test with esbuild (already present as a Vite
 * dependency), injecting the recordings and the API base URL, then runs it.
 *
 * The entry point is .tsx because the deletion tests render the real components
 * with react-dom/server rather than asserting against a copy of their markup.
 * Nothing else is needed for that: React 19's server renderer bundles for Node
 * on its own, and the delete dialog's effects (focus trap, scroll lock) are
 * no-ops in a static render.
 *
 * Recordings come from scripts/verify_integration.py, which must be run first:
 *
 *   cd backend && python ../scripts/verify_integration.py
 *   cd ../frontend && npm run verify:contract
 */

import { readFileSync, existsSync } from 'node:fs'
import { fileURLToPath } from 'node:url'
import { dirname, resolve } from 'node:path'
import { build } from 'esbuild'

const here = dirname(fileURLToPath(import.meta.url))
const repoRoot = resolve(here, '..', '..')
/**
 * A real file URL for the CommonJS bridge below.
 *
 * The bundle is imported as a data: URL, so `import.meta.url` inside it is that
 * data URL and cannot anchor module resolution. React's Node server renderer
 * lazily `require`s a couple of builtins (`util`, `stream`), which is a hard
 * failure in an ESM module with no `require` in scope, so one is supplied.
 */
const selfUrl = import.meta.url

const recordingsPath =
  process.env.PRAMAAN_RECORDINGS ?? resolve(repoRoot, 'reports', 'integration-recordings.json')

if (!existsSync(recordingsPath)) {
  console.error(`No recordings at ${recordingsPath}.

The contract tests replay real backend responses rather than mocks, so the
backend verifier has to run first:

    cd backend && source ../.venv/bin/activate
    python ../scripts/verify_integration.py
`)
  process.exit(1)
}

const recordings = readFileSync(recordingsPath, 'utf8')
const apiBase = process.env.VITE_API_URL ?? 'http://127.0.0.1:8000'

const result = await build({
  entryPoints: [resolve(here, 'contract.test.tsx')],
  bundle: true,
  write: false,
  platform: 'node',
  format: 'esm',
  target: 'node20',
  jsx: 'automatic',
  banner: {
    js: [
      "import { createRequire as __createRequire } from 'node:module'",
      `const require = __createRequire(${JSON.stringify(selfUrl)})`,
    ].join('\n'),
  },
  // The client reads its base URL from import.meta.env; supply it the way Vite
  // would, so config.ts is exercised as written rather than patched.
  define: {
    'import.meta.env.VITE_API_URL': JSON.stringify(apiBase),
    'import.meta.env': JSON.stringify({ VITE_API_URL: apiBase }),
    __RECORDINGS__: JSON.stringify(recordings),
  },
})

const bundled = result.outputFiles[0].text
const dataUrl = `data:text/javascript;base64,${Buffer.from(bundled).toString('base64')}`

globalThis.__RECORDINGS__ = recordings
await import(dataUrl)
