import { describe, expect, it } from 'vitest'

import { resolveMarkdownAsset } from './resultPaths'

describe('resolveMarkdownAsset', () => {
  const manifest = ['images/page.jpg', 'markdowns/local.png']

  it('resolves a sibling result without leaving the manifest', () => {
    expect(
      resolveMarkdownAsset(
        'markdowns/document.md',
        '../images/page.jpg',
        manifest,
      ),
    ).toBe('images/page.jpg')
    expect(
      resolveMarkdownAsset('markdowns/document.md', './local.png', manifest),
    ).toBe('markdowns/local.png')
  })

  it('rejects remote, root-relative, escaping, and unlisted paths', () => {
    expect(
      resolveMarkdownAsset(
        'document.md',
        'https://example.test/a.png',
        manifest,
      ),
    ).toBeNull()
    expect(
      resolveMarkdownAsset('document.md', '/images/page.jpg', manifest),
    ).toBeNull()
    expect(
      resolveMarkdownAsset('document.md', '../images/page.jpg', manifest),
    ).toBeNull()
    expect(
      resolveMarkdownAsset('document.md', 'images/missing.jpg', manifest),
    ).toBeNull()
  })
})
