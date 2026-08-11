export function resolveMarkdownAsset(
  markdownPath: string,
  assetPath: string,
  publicFiles: readonly string[],
): string | null {
  if (!assetPath || /^(?:[a-z]+:|\/)/i.test(assetPath)) return null

  const stack = markdownPath.split('/').filter(Boolean).slice(0, -1)
  const cleanPath = assetPath.split(/[?#]/, 1)[0]
  for (const segment of cleanPath.split('/')) {
    if (!segment || segment === '.') continue
    if (segment === '..') {
      if (stack.length === 0) return null
      stack.pop()
      continue
    }
    stack.push(segment)
  }
  const resolved = stack.join('/')
  return publicFiles.includes(resolved) ? resolved : null
}
