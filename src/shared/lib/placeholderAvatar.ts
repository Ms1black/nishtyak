const PALETTE = ['#6C7BFF', '#9B7BFF', '#FF8A65', '#FFC857', '#4FD1A5', '#56B6F7', '#F06B9A', '#B5E655']

function hash(seed: string): number {
  let h = 2166136261
  for (let i = 0; i < seed.length; i++) {
    h ^= seed.charCodeAt(i)
    h = Math.imul(h, 16777619)
  }
  return h >>> 0
}

function random(seed: string) {
  let a = hash(seed)
  return () => {
    a = (a + 0x6d2b79f5) | 0
    let t = Math.imul(a ^ (a >>> 15), 1 | a)
    t = (t + Math.imul(t ^ (t >>> 7), 61 | t)) ^ t
    return ((t ^ (t >>> 14)) >>> 0) / 4294967296
  }
}

const pick = <T>(rnd: () => number, list: T[]): T => list[Math.floor(rnd() * list.length)]!

const toDataUri = (svg: string) => `data:image/svg+xml;utf8,${encodeURIComponent(svg)}`

export function createUserAvatar(seed: string): string {
  const rnd = random(`user:${seed}`)
  const colors = [pick(rnd, PALETTE), pick(rnd, PALETTE), pick(rnd, PALETTE)]
  const blobs = colors
    .map((color) => {
      const cx = Math.round(10 + rnd() * 60)
      const cy = Math.round(10 + rnd() * 60)
      const r = Math.round(26 + rnd() * 20)
      return `<circle cx="${cx}" cy="${cy}" r="${r}" fill="${color}"/>`
    })
    .join('')
  return toDataUri(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80"><defs><filter id="b" x="-50%" y="-50%" width="200%" height="200%"><feGaussianBlur stdDeviation="12"/></filter></defs><rect width="80" height="80" fill="#1f1f1f"/><g filter="url(#b)" opacity="0.9">${blobs}</g></svg>`,
  )
}

export function createTeamAvatar(seed: string): string {
  const rnd = random(`team:${seed}`)
  const color = pick(rnd, PALETTE)
  const cell = 12
  const offset = 10
  let cells = ''
  for (let y = 0; y < 5; y++) {
    for (let x = 0; x < 3; x++) {
      if (rnd() < 0.5) continue
      const mirrored = x === 2 ? [x] : [x, 4 - x]
      for (const mx of mirrored) {
        cells += `<rect x="${offset + mx * cell}" y="${offset + y * cell}" width="${cell}" height="${cell}"/>`
      }
    }
  }
  return toDataUri(
    `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 80 80" shape-rendering="crispEdges"><rect width="80" height="80" fill="#141414"/><g fill="${color}">${cells}</g></svg>`,
  )
}
