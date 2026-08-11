import { ChevronLeftIcon, ChevronRightIcon } from '@radix-ui/react-icons'
import { Button, Callout, Flex, Spinner, Text } from '@radix-ui/themes'
import { useCallback, useEffect, useRef, useState } from 'react'
import { Document, Page, pdfjs } from 'react-pdf'
import workerUrl from 'pdfjs-dist/build/pdf.worker.min.mjs?url'

import type { OcrMode } from '../types'
import { CropImage } from './CropImage'

pdfjs.GlobalWorkerOptions.workerSrc = workerUrl

interface MediaPreviewProps {
  file: File
  mode: OcrMode
  onSelection: (file: File | null) => void
}

export function MediaPreview({ file, mode, onSelection }: MediaPreviewProps) {
  const [objectUrl] = useState(() => URL.createObjectURL(file))

  useEffect(() => {
    return () => URL.revokeObjectURL(objectUrl)
  }, [objectUrl])

  if (
    file.type === 'application/pdf' ||
    file.name.toLowerCase().endsWith('.pdf')
  ) {
    return (
      <PdfPreview
        key={`${objectUrl}-${mode}`}
        fileUrl={objectUrl}
        filename={file.name}
        mode={mode}
        onSelection={onSelection}
      />
    )
  }
  if (mode === 'parse') {
    return (
      <img src={objectUrl} alt="上传文件预览" className="document-preview" />
    )
  }
  return (
    <CropImage
      source={objectUrl}
      filename={file.name}
      onSelection={onSelection}
    />
  )
}

interface PdfPreviewProps {
  fileUrl: string
  filename: string
  mode: OcrMode
  onSelection: (file: File | null) => void
}

function PdfPreview({ fileUrl, filename, mode, onSelection }: PdfPreviewProps) {
  const canvasRef = useRef<HTMLCanvasElement | null>(null)
  const [pageNumber, setPageNumber] = useState(1)
  const [pageCount, setPageCount] = useState(0)
  const [snapshot, setSnapshot] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  const rendered = useCallback(() => {
    if (mode !== 'parse' && canvasRef.current) {
      setSnapshot(canvasRef.current.toDataURL('image/png'))
    }
  }, [mode])

  function changePage(nextPage: number) {
    setSnapshot(null)
    onSelection(null)
    setPageNumber(nextPage)
  }

  return (
    <Flex direction="column" gap="3" width="100%" align="center">
      <Document
        file={fileUrl}
        onLoadSuccess={({ numPages }) => {
          setPageCount(numPages)
          setError(null)
        }}
        onLoadError={() => setError('PDF 加载失败，请确认文件未损坏。')}
        loading={<Spinner size="3" />}
      >
        <div className={mode === 'parse' ? 'pdf-page' : 'pdf-render-source'}>
          <Page
            pageNumber={pageNumber}
            width={760}
            renderAnnotationLayer={false}
            renderTextLayer={false}
            canvasRef={canvasRef}
            onRenderSuccess={rendered}
            loading={<Spinner size="3" />}
          />
        </div>
      </Document>
      {mode !== 'parse' && snapshot ? (
        <CropImage
          key={`${fileUrl}-${pageNumber}`}
          source={snapshot}
          filename={filename}
          onSelection={onSelection}
        />
      ) : null}
      {pageCount > 0 ? (
        <Flex align="center" gap="3">
          <Button
            variant="soft"
            disabled={pageNumber <= 1}
            onClick={() => changePage(pageNumber - 1)}
          >
            <ChevronLeftIcon /> 上一页
          </Button>
          <Text size="2">
            第 {pageNumber} / {pageCount} 页
          </Text>
          <Button
            variant="soft"
            disabled={pageNumber >= pageCount}
            onClick={() => changePage(pageNumber + 1)}
          >
            下一页 <ChevronRightIcon />
          </Button>
        </Flex>
      ) : null}
      {mode !== 'parse' ? (
        <Text size="2" color="gray">
          单项识别会把当前 PDF 页转为图片，再提交选中的区域。
        </Text>
      ) : null}
      {error ? (
        <Callout.Root color="red">
          <Callout.Text>{error}</Callout.Text>
        </Callout.Root>
      ) : null}
    </Flex>
  )
}
