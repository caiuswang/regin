/**
 * Identifiers of the rows `scripts/e2e-seed.py` seeds into the scratch DB
 * before the server starts. Specs that need "a session that exists" reference
 * these instead of a UUID copied out of someone's real database.
 */
export const BASELINE_TRACE = 'e2e-baseline-session'

/** The live card's worst-case fixture: 19 turns, long unbroken strings, system spans. */
export const HEAVY_TRACE = 'e2e-heavy-session'

/**
 * The patterns directory the server under test actually writes to.
 *
 * Derived from the scratch `REGIN_DATA_DIR` rather than hardcoded to
 * `~/.local/share/regin/patterns`: the import specs `rm -rf` slugs under this
 * path, and against the real directory that deleted the operator's patterns.
 */
export const PATTERNS_DIR = process.env.REGIN_DATA_DIR
  ? `${process.env.REGIN_DATA_DIR}/patterns`
  : `${process.env.HOME}/.local/share/regin/patterns`
