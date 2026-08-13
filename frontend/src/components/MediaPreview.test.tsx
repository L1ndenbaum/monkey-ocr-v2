import { render } from '@testing-library/react'
import type { ReactNode } from 'react'
import { beforeEach, describe, expect, it, vi } from 'vitest'

import { MediaPreview } from './MediaPreview'

const { documentFile } = vi.hoisted(() => ({
  documentFile: vi.fn(),
}))

vi.mock('pdfjs-dist/build/pdf.worker.min.mjs?url', () => ({
  default: '/pdf.worker.mjs',
}))

vi.mock('react-pdf', () => ({
  Document: ({ children, file }: { children?: ReactNode; file?: unknown }) => {
    documentFile(file)
    return <div>{children}</div>
  },
  Page: () => <canvas />,
  pdfjs: { GlobalWorkerOptions: {} },
}))

describe('MediaPreview', () => {
  const createObjectURL = vi.fn(() => 'blob:image-preview')
  const revokeObjectURL = vi.fn()

  beforeEach(() => {
    documentFile.mockClear()
    createObjectURL.mockClear()
    revokeObjectURL.mockClear()
    Object.defineProperties(URL, {
      createObjectURL: { configurable: true, value: createObjectURL },
      revokeObjectURL: { configurable: true, value: revokeObjectURL },
    })
  })

  it('passes the original PDF file to React-PDF without creating a blob URL', () => {
    const file = new File(['pdf'], 'sample.pdf', {
      type: 'application/pdf',
    })

    render(<MediaPreview file={file} mode="parse" onSelection={vi.fn()} />)

    expect(documentFile).toHaveBeenCalledWith(file)
    expect(createObjectURL).not.toHaveBeenCalled()
  })

  it('keeps object URLs scoped to image previews', () => {
    const file = new File(['image'], 'sample.png', { type: 'image/png' })

    const { unmount } = render(
      <MediaPreview file={file} mode="parse" onSelection={vi.fn()} />,
    )

    expect(createObjectURL).toHaveBeenCalledWith(file)
    expect(documentFile).not.toHaveBeenCalled()

    unmount()
    expect(revokeObjectURL).toHaveBeenCalledWith('blob:image-preview')
  })
})
