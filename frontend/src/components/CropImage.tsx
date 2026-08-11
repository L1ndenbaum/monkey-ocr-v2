import { Callout, Flex, Text } from '@radix-ui/themes'
import { useRef, useState } from 'react'
import ReactCrop, { type Crop, type PixelCrop } from 'react-image-crop'

import { cropImageToFile } from '../crop'

interface CropImageProps {
  source: string
  filename: string
  onSelection: (file: File | null) => void
}

const initialCrop: Crop = { unit: '%', x: 10, y: 10, width: 80, height: 80 }

export function CropImage({ source, filename, onSelection }: CropImageProps) {
  const imageRef = useRef<HTMLImageElement | null>(null)
  const [crop, setCrop] = useState<Crop>(initialCrop)
  const [error, setError] = useState<string | null>(null)

  async function complete(pixelCrop: PixelCrop) {
    const image = imageRef.current
    if (!image || pixelCrop.width < 2 || pixelCrop.height < 2) {
      onSelection(null)
      return
    }
    try {
      onSelection(await cropImageToFile(image, pixelCrop, filename))
      setError(null)
    } catch (caught) {
      setError(caught instanceof Error ? caught.message : '无法生成裁剪图片。')
      onSelection(null)
    }
  }

  return (
    <Flex direction="column" gap="3" align="center">
      <ReactCrop
        crop={crop}
        onChange={setCrop}
        onComplete={complete}
        keepSelection
      >
        <img
          ref={imageRef}
          src={source}
          alt="待识别区域"
          className="crop-image"
          draggable={false}
        />
      </ReactCrop>
      <Text size="2" color="gray">
        拖动边框选择识别区域；未选择时会使用完整图片。
      </Text>
      {error ? (
        <Callout.Root color="red" size="1">
          <Callout.Text>{error}</Callout.Text>
        </Callout.Root>
      ) : null}
    </Flex>
  )
}
