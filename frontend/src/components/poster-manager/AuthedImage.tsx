import { useEffect, useRef, useState } from 'react'

type AuthedImageProps = {
  // Loader that fetches the image bytes (with the auth header) as a Blob.
  load: () => Promise<Blob>
  // Re-fetch whenever any of these change.
  deps: unknown[]
  alt: string
  className?: string
  // When true, clicking the image opens a larger lightbox view.
  expandable?: boolean
  // Delay the (re)fetch by this many ms, resetting on each deps change. Avoids one
  // request per keystroke when deps change rapidly (e.g. a live style preview).
  debounceMs?: number
}

// Renders an image that requires the Authorization header (so a plain <img src>
// would 401). Fetches it as a Blob, shows an object URL, and revokes on change/unmount.
function AuthedImage({ load, deps, alt, className, expandable = false, debounceMs = 0 }: AuthedImageProps) {
  const [url, setUrl] = useState<string | null>(null)
  const [error, setError] = useState(false)
  const [zoomed, setZoomed] = useState(false)
  const loadRef = useRef(load)
  loadRef.current = load

  useEffect(() => {
    let cancelled = false
    let objectUrl: string | null = null
    setError(false)
    const run = () => {
      loadRef.current()
        .then((blob) => {
          if (cancelled) return
          objectUrl = URL.createObjectURL(blob)
          setUrl(objectUrl)
        })
        .catch(() => {
          if (!cancelled) setError(true)
        })
    }
    let timer: ReturnType<typeof setTimeout> | undefined
    if (debounceMs > 0) {
      timer = setTimeout(run, debounceMs)
    } else {
      run()
    }
    return () => {
      cancelled = true
      if (timer) clearTimeout(timer)
      if (objectUrl) URL.revokeObjectURL(objectUrl)
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, debounceMs])

  // Close the lightbox on Escape.
  useEffect(() => {
    if (!zoomed) return
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setZoomed(false)
    }
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [zoomed])

  if (error) {
    return <div className={`authed-image authed-image-error ${className ?? ''}`}>No preview</div>
  }
  if (!url) {
    return <div className={`authed-image authed-image-loading ${className ?? ''}`} />
  }
  return (
    <>
      <img
        src={url}
        alt={alt}
        className={className}
        onClick={expandable ? () => setZoomed(true) : undefined}
        onKeyDown={
          expandable
            ? (e) => {
                if (e.key === 'Enter' || e.key === ' ') {
                  e.preventDefault()
                  setZoomed(true)
                }
              }
            : undefined
        }
        role={expandable ? 'button' : undefined}
        tabIndex={expandable ? 0 : undefined}
        aria-label={expandable ? `${alt} — enlarge` : undefined}
        style={expandable ? { cursor: 'zoom-in' } : undefined}
      />
      {zoomed && (
        <div
          className="authed-image-lightbox"
          onClick={() => setZoomed(false)}
          role="dialog"
          aria-modal="true"
          aria-label={alt}
        >
          <img src={url} alt={alt} className="authed-image-lightbox-img" />
        </div>
      )}
    </>
  )
}

export default AuthedImage
