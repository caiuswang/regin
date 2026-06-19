import { clsx } from 'clsx'
import { twMerge } from 'tailwind-merge'

/**
 * Merge conditional class lists and de-conflict Tailwind utilities.
 * The shadcn-style helper every UI primitive uses so a caller-supplied
 * `class` can override a primitive's defaults (last write wins).
 *
 *   cn('px-4 py-2', condition && 'bg-primary', props.class)
 */
export function cn(...inputs) {
  return twMerge(clsx(inputs))
}
