import { rmSync } from 'node:fs'

import { SCRATCH_DIR } from './e2e-env.js'

export default function globalTeardown() {
  rmSync(SCRATCH_DIR, { recursive: true, force: true })
}
