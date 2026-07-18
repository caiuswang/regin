/**
 * Dev-only frontend debugging aids. Imported dynamically from main.js behind
 * `import.meta.env.DEV`, so nothing here reaches a production bundle.
 */

import { installSourceStamp } from './source-stamp.js'
import { installGrab } from './grab.js'

export function installDevTools(app) {
  installSourceStamp(app)
  installGrab()
}
