// The pixel theme's sprite sheet. A sprite is a string grid ('.' = empty) and
// a palette mapping each grid letter to a palette KEY, never a hex: PixelSprite
// paints key `b` with var(--pl-px-b), so light/dark live in tokens.css. The keys
// are the design's sprite palette: k outline, w face, s steel, l light steel,
// b bot blue, n wood, and the status hues a y r g v.

const INK = { k: 'k', p: 'w' }

const BOT = [
  '.....k.....',
  '.....k.....',
  '..kkkkkkk..',
  '..kpppppk..',
  '..kpkpkpk..',
  '..kpppppk..',
  '..kkkkkkk..',
  '.kbbbbbbbk.',
  'kkbbpbpbbkk',
  '.kbbbbbbbk.',
  '..kk...kk..',
  '..kk...kk..',
]

const BOT_KO = [
  '.....k.....',
  '.....k.....',
  '..kkkkkkk..',
  '..kpppppk..',
  '..kkpppkk..',
  '..kpkpkpk..',
  '..kkkkkkk..',
  '.kbbbbbbbk.',
  'kkbbpbpbbkk',
  '.kbbbbbbbk.',
  '..kk...kk..',
  '..kk...kk..',
]

const BUG = [
  '.k.......k.',
  '..k.....k..',
  '...kkkkk...',
  '..kakakak..',
  'kkaaaaaaakk',
  '..kakakak..',
  'kkaaaaaaakk',
  '..kaaaaak..',
  '...kkkkk...',
]

const FLAG = [
  'kk........',
  'kkcccccc..',
  'kkcccccccc',
  'kkcccccc..',
  'kk........',
  'kk........',
  'kk........',
  'kk........',
  'kk........',
  'kk........',
  'kkkk......',
  'kkkk......',
]

export const SPRITES = {
  bot: { grid: BOT, palette: { ...INK, b: 'b' } },
  'bot.running': { grid: BOT, palette: { ...INK, b: 'v' } },
  'bot.failed': { grid: BOT_KO, palette: { ...INK, b: 'r' } },
  bug: { grid: BUG, palette: { ...INK, a: 'a' } },
  'bug.cold': { grid: BUG, palette: { ...INK, a: 's' } },
  flag: { grid: FLAG, palette: { ...INK, c: 'g' } },
  'flag.escaped': { grid: FLAG, palette: { ...INK, c: 'a' } },
  'flag.cold': { grid: FLAG, palette: { ...INK, c: 's' } },
  bulb: {
    grid: [
      '..kkkkk..',
      '.kyyyyyk.',
      'kyypyyyyk',
      'kypyyyyyk',
      'kyyyyyyyk',
      '.kyyyyyk.',
      '..kyyyk..',
      '..kkkkk..',
      '..ksssk..',
      '..kkkkk..',
      '...kkk...',
    ],
    palette: { ...INK, y: 'y', s: 's' },
  },
  lock: {
    grid: [
      '..kkkkk..',
      '.kk...kk.',
      '.k.....k.',
      '.k.....k.',
      'kkkkkkkkk',
      'kaaaaaaak',
      'kaaakaaak',
      'kaaakaaak',
      'kaaaaaaak',
      'kkkkkkkkk',
    ],
    palette: { ...INK, a: 'a' },
  },
  hammer: {
    grid: [
      'kkkkkkkkk',
      'ksssssssk',
      'ksssssssk',
      'kkkkkkkkk',
      '...kak...',
      '...kak...',
      '...kak...',
      '...kak...',
      '...kak...',
      '...kkk...',
    ],
    palette: { ...INK, s: 's', a: 'a' },
  },
  scroll: {
    grid: [
      'kkkkkkkkk',
      'kpppppppk',
      'kpkkkkkpk',
      'kpppppppk',
      'kpkkkkppk',
      'kpppppppk',
      'kpkkkkkpk',
      'kpppppppk',
      'kpkkkpppk',
      'kpppppppk',
      'kkkkkkkkk',
    ],
    palette: INK,
  },
  check: {
    grid: [
      '.........kk',
      '........kgk',
      '.......kgk.',
      'kk....kgk..',
      'kgk..kgk...',
      '.kgkkgk....',
      '..kggk.....',
      '...kk......',
    ],
    palette: { ...INK, g: 'g' },
  },
  grid: {
    grid: [
      'kkkkkkkkkkkkk',
      'kpppkpppkpppk',
      'kpppkpppkpppk',
      'kkkkkkkkkkkkk',
      'kpppkpppkpppk',
      'kpppkpppkpppk',
      'kkkkkkkkkkkkk',
    ],
    palette: INK,
  },
  tray: {
    grid: [
      '.kkkkkkkkkkk.',
      '.k.........k.',
      'k...........k',
      'kkkkk...kkkkk',
      'kpppkkkkkpppk',
      'kpppppppppppk',
      'kkkkkkkkkkkkk',
    ],
    palette: INK,
  },
  // The loop scene's three stations.
  board: {
    grid: [
      'kkkkkkkkkkkkk',
      'kpbbpyypvvppk',
      'kpppppppppppk',
      'kpbbpppppggpk',
      'kpppppppppppk',
      'kkkkkkkkkkkkk',
      '..k.......k..',
      '..k.......k..',
    ],
    palette: { ...INK, b: 'b', y: 'y', v: 'v', g: 'g' },
  },
  anvil: {
    grid: [
      'kkkkkkkkkkk',
      'ksssssssssk',
      '.kkkkkkkkk.',
      '...ksssk...',
      '...ksssk...',
      '..kkkkkkk..',
      '.ksssssssk.',
      'kkkkkkkkkkk',
    ],
    palette: { ...INK, s: 's' },
  },
  den: {
    grid: [
      '....kkkkk....',
      '..kkssssskk..',
      '.ksskkkkkssk.',
      '.ksk.....ksk.',
      'ksk.......ksk',
      'ksk.......ksk',
      'ksk.......ksk',
      'kkk.......kkk',
    ],
    palette: { ...INK, s: 's' },
  },
}

// Priority as four rising bars, the lit ones in the priority's hue (the same
// count TaskPriorityIcon draws): urgent 4, high 3, medium 2, low 1, none 0.
const BAR_HUE = { 4: 'r', 3: 'a', 2: 'b', 1: 'k', 0: 's' }
function bars(lit) {
  const grid = [3, 2, 1, 0].map(row => [1, 2, 3, 4]
    .map(bar => (bar > row ? (bar <= lit ? 'o' : 's') : '.')).join('.'))
  return { grid, palette: { o: BAR_HUE[lit], s: 's' } }
}
for (const lit of [0, 1, 2, 3, 4]) SPRITES[`bars.${lit}`] = bars(lit)

SPRITES.coin = {
  grid: [
    '..kkk..',
    '.kyyyk.',
    'kyypyyk',
    'kyypyyk',
    'kyypyyk',
    '.kyyyk.',
    '..kkk..',
  ],
  palette: { ...INK, y: 'y' },
}

// One <rect> per horizontal run of a single colour, so a 12×12 sprite is a
// few dozen nodes rather than a hundred.
export function spriteRuns(sprite) {
  const runs = []
  sprite.grid.forEach((row, y) => {
    let x = 0
    while (x < row.length) {
      const ch = row[x]
      let end = x + 1
      while (end < row.length && row[end] === ch) end++
      if (ch !== '.') runs.push({ x, y, w: end - x, key: sprite.palette[ch] })
      x = end
    }
  })
  return runs
}

export function spriteSize(sprite) {
  return { w: Math.max(...sprite.grid.map(r => r.length)), h: sprite.grid.length }
}
