import { useEffect, useRef, useState } from 'react'
import { Upload } from 'lucide-react'
import { fetchCustomBadge, uploadCustomBadge } from '../../api/overlays'
import { getApiErrorMessage } from '../../api/client'

type Props = {
  // The uploaded picture the badge draws ('' = none yet). Every upload gets a new id.
  imageId: string
  onChange: (imageId: string) => void
  id: string
  // Whose picture it is, for the button's name.
  name: string
}

// One badge's own picture, on one line of its row: the picture, and a button to upload or replace it.
function BadgeImage({ imageId, onChange, id, name }: Props) {
  const [thumb, setThumb] = useState<string | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')
  const fileInput = useRef<HTMLInputElement | null>(null)

  // The picture as an object URL (the API needs the auth header).
  useEffect(() => {
    setThumb(null)
    setError('')  // another picture: what was said about the last one no longer holds
    if (!imageId) return
    let cancelled = false
    let url: string | null = null
    fetchCustomBadge(imageId)
      .then((blob) => {
        if (cancelled) return
        url = URL.createObjectURL(blob)
        setThumb(url)
      })
      .catch(() => {
        if (!cancelled) setError('That image is gone. Upload it again.')
      })
    return () => {
      cancelled = true
      if (url) URL.revokeObjectURL(url)
    }
  }, [imageId])

  const upload = async (file: File) => {
    setBusy(true)
    setError('')
    try {
      const result = await uploadCustomBadge(file)
      onChange(result.id)
    } catch (err) {
      setError(getApiErrorMessage(err, 'The image upload failed'))
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="overlay-badge-image">
      <input
        ref={fileInput}
        type="file"
        accept="image/png,image/webp,image/*"
        aria-label={`${name} image file`}
        hidden
        onChange={(e) => {
          const file = e.target.files?.[0]
          e.target.value = ''
          if (file) void upload(file)
        }}
      />
      <span className="overlay-image-thumb" title={imageId ? undefined : 'No image yet'}>{thumb ? <img src={thumb} alt={`${name} image`} /> : null}</span>
      <button id={id} type="button" className="btn-toolbar btn-toolbar-link btn-xs" disabled={busy} aria-label={`${imageId ? 'Replace' : 'Upload'} ${name} image`} onClick={() => fileInput.current?.click()}>
        <Upload size={12} /> {imageId ? 'Replace' : 'Upload'}
      </button>
      {error && <span className="overlay-image-status error" role="status">{error}</span>}
    </div>
  )
}

export default BadgeImage
