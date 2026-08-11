import type { PixelCrop } from 'react-image-crop'

export async function cropImageToFile(
  image: HTMLImageElement,
  crop: PixelCrop,
  originalName: string,
): Promise<File> {
  const scaleX = image.naturalWidth / image.width
  const scaleY = image.naturalHeight / image.height
  const canvas = document.createElement('canvas')
  canvas.width = Math.max(1, Math.round(crop.width * scaleX))
  canvas.height = Math.max(1, Math.round(crop.height * scaleY))

  const context = canvas.getContext('2d')
  if (!context) throw new Error('浏览器无法创建裁剪画布。')
  context.imageSmoothingEnabled = true
  context.imageSmoothingQuality = 'high'
  context.drawImage(
    image,
    crop.x * scaleX,
    crop.y * scaleY,
    crop.width * scaleX,
    crop.height * scaleY,
    0,
    0,
    canvas.width,
    canvas.height,
  )

  const blob = await new Promise<Blob>((resolve, reject) => {
    canvas.toBlob(
      (value) =>
        value ? resolve(value) : reject(new Error('无法生成裁剪图片。')),
      'image/png',
      0.95,
    )
  })
  const baseName = originalName.replace(/\.[^.]+$/, '') || 'selection'
  return new File([blob], `${baseName}-selection.png`, { type: 'image/png' })
}
