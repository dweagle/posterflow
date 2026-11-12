import { useCallback, useEffect, useRef, useState } from 'react'
import { RotateCcw, Upload } from 'lucide-react'
import { deleteOverlayImage, fetchOverlayImage, getOverlayImages, uploadOverlayImage } from '../../api/overlays'
import type { OverlayImage, OverlayImageSet } from '../../api/overlays'
import { getApiErrorMessage } from '../../api/client'
import ListPicker from './ListPicker'

type Props = {
  // The overlay type whose files these are (streaming, content_rating).
  typeKey: string
  idPrefix: string
  // The set the overlay draws (its set option): uploads and thumbnails follow it. Unset for a
  // type with a single set (the server uses its default).
  imageSet?: OverlayImageSet
  // What one file is called: logo, badge.
  noun?: string
  // The files changed on the server: the preview should fetch again.
  onChanged?: () => void
}

const marker = (image: OverlayImage, noun: string, fallback: string) =>
  image.source === 'custom' ? ' (custom)' : image.source === 'fallback' ? ` (${fallback.toLowerCase()} ${noun})` : ''

// Pick one of a type's image badges, see the file it draws right now in the chosen set, and
// replace it with your own image (or go back to the bundled one). Files live in
// config/overlays/<type>/<set>/.
function ImageUploads({ typeKey, idPrefix, imageSet, noun = 'logo', onChanged }: Props) {
  const [list, setList] = useState<OverlayImage[]>([])
  // The type `list` belongs to. Until the list of the type on screen has arrived, nothing of
  // another type is shown or asked for (the picker is empty and disabled, with no thumbnail).
  const [listType, setListType] = useState('')
  const [setLabel, setSetLabel] = useState('')
  const [fallbackLabel, setFallbackLabel] = useState('')
  const [selected, setSelected] = useState('')
  const [thumb, setThumb] = useState<string | null>(null)
  const [version, setVersion] = useState(0)
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState<{ text: string; error: boolean } | null>(null)
  const fileInput = useRef<HTMLInputElement | null>(null)

  const loadSeq = useRef(0)
  const load = useCallback(async () => {
    const seq = ++loadSeq.current
    try {
      const result = await getOverlayImages(typeKey, imageSet)
      if (seq !== loadSeq.current) return  // another set or type was asked for since: that answer wins
      setList(result.images)
      setListType(typeKey)
      setSetLabel(result.set_label)
      setFallbackLabel(result.fallback_label ?? '')
      setSelected((prev) => (prev && result.images.some((l) => l.key === prev) ? prev : result.images[0]?.key ?? ''))
    } catch (err) {
      if (seq === loadSeq.current) setStatus({ text: getApiErrorMessage(err, 'The image request failed'), error: true })
    }
  }, [typeKey, imageSet])

  useEffect(() => {
    setStatus(null)
    void load()
  }, [load])

  const ready = listType === typeKey
  const images = ready ? list : []

  // The current file of the selected key, as an object URL (the API needs the auth header).
  useEffect(() => {
    if (!selected || !ready) return
    let cancelled = false
    let url: string | null = null
    fetchOverlayImage(typeKey, selected, imageSet)
      .then((blob) => {
        if (cancelled) return
        url = URL.createObjectURL(blob)
        setThumb(url)
      })
      .catch(() => {
        if (!cancelled) setThumb(null)
      })
    return () => {
      cancelled = true
      if (url) URL.revokeObjectURL(url)
    }
  }, [typeKey, selected, version, imageSet, ready])

  const current = images.find((l) => l.key === selected)
  const name = current?.label ?? selected
  // "logo (White)" where the type has several sets; just "badge" where it has one.
  const which = setLabel && imageSet ? `${noun} (${setLabel})` : noun

  const changed = () => {
    setVersion((v) => v + 1)
    onChanged?.()
  }

  const upload = async (file: File) => {
    if (!selected) return
    setBusy(true)
    setStatus(null)
    try {
      const result = await uploadOverlayImage(typeKey, selected, file, imageSet)
      setStatus({ text: `${name} ${which} now uses ${file.name} (${result.width} x ${result.height})`, error: false })
      await load()
      changed()
    } catch (err) {
      setStatus({ text: getApiErrorMessage(err, 'The image request failed'), error: true })
    } finally {
      setBusy(false)
    }
  }

  const restore = async () => {
    if (!selected) return
    setBusy(true)
    setStatus(null)
    try {
      await deleteOverlayImage(typeKey, selected, imageSet)
      setStatus({ text: `${name} ${which} is the bundled one again`, error: false })
      await load()
      changed()
    } catch (err) {
      setStatus({ text: getApiErrorMessage(err, 'The image request failed'), error: true })
    } finally {
      setBusy(false)
    }
  }

  return (
    <div className="overlay-image-uploads">
      {/* The picker with its buttons on one line; the file it draws now underneath, however wide. */}
      <div className="overlay-image-row">
        {/* Its own list, not the browser's menu: a long list of badges needs more room than a small screen has. */}
        <ListPicker
          id={`${idPrefix}-key`}
          value={selected}
          options={images.map((l) => ({ value: l.key, label: `${l.label}${marker(l, noun, fallbackLabel)}` }))}
          disabled={busy || images.length === 0}
          onChange={(key) => { setSelected(key); setStatus(null) }}
        />
        <input
          ref={fileInput}
          type="file"
          accept="image/png,image/webp,image/*"
          aria-label="Image file"
          hidden
          onChange={(e) => {
            const file = e.target.files?.[0]
            e.target.value = ''
            if (file) void upload(file)
          }}
        />
        <button type="button" className="btn-toolbar btn-toolbar-link btn-xs" disabled={busy || !selected} onClick={() => fileInput.current?.click()}>
          <Upload size={12} /> Upload PNG
        </button>
        {current?.custom && (
          <button type="button" className="btn-toolbar btn-toolbar-link btn-xs" disabled={busy} onClick={() => void restore()}>
            <RotateCcw size={12} /> Use bundled
          </button>
        )}
      </div>
      <span className="overlay-image-thumb" title={current?.source === 'custom' ? `Your custom ${noun}` : current?.source === 'fallback' ? `No ${setLabel.toLowerCase()} ${noun} for this one: the ${fallbackLabel.toLowerCase()} one is drawn` : `The bundled ${noun}`}>
        {thumb && ready ? <img src={thumb} alt={`${name} ${noun}`} /> : null}
      </span>
      {status && <span className={`overlay-image-status ${status.error ? 'error' : ''}`} role="status">{status.text}</span>}
    </div>
  )
}

export default ImageUploads
