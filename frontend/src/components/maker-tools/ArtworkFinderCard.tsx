import { useCallback, useState, type CSSProperties, type ReactNode } from 'react'
import { Check, ChevronDown, ChevronUp, Clapperboard as MovieIcon, Copy, Crop as CropIcon, Download, FolderOpen, Globe, Image as ImageIcon, Plus, Sparkles, Tv, Type as TypeIcon, Wand2 } from 'lucide-react'
import {
  addArtwork,
  cropArtworkSquare,
  getApiErrorMessage,
  getArtworkCandidates,
  getGracenoteImageProxyUrl,
  getTmdbImageProxyUrl,
  getTvdbImageProxyUrl,
  type ArtworkCandidate,
  type ArtworkCandidatesResponse,
  type ArtworkListType,
  type ArtworkSubtype,
  type ImageSource,
  type TmdbSearchResult,
} from '../../api/client'
import { useToast } from '../Toast'
import SquareCropModal from './SquareCropModal'
import LocalArtworkPickerModal from './LocalArtworkPickerModal'
import TextLogoModal from './TextLogoModal'
import { TMDB_IMAGE_LANGUAGES } from './TmdbItemCard'
import { useTvdbEnabled } from '../../hooks/useTvdbEnabled'
import ServiceLinks from './ServiceLinks'
import { useCardOverview } from '../../hooks/useCardOverview'
import tmdbIcon from '../../assets/service-icons/tmdb.png'
import tvdbIcon from '../../assets/service-icons/tvdb.png'

type Props = {
  item: TmdbSearchResult
  /** Index into the full IDarr sync_targets list of the chosen artwork scope, or null if none. */
  syncTargetIndex: number | null
  scopeLabel: string | null
  /** Artwork types this item is missing (unmatched view) — shown as badges in the card header. */
  missing?: ArtworkSubtype[]
  /** Extra content for the card's info column (under the overview) — the unmatched view slots
   *  its resolve-on-TMDB flow here for id-less collections. */
  infoExtra?: ReactNode
}

const MISSING_LABEL: Record<ArtworkSubtype, string> = {
  logo: 'Logo',
  background: 'Backdrop',
  squareart: 'Square Art',
}

const SECTIONS: { key: ArtworkListType; label: string }[] = [
  { key: 'logo', label: 'Logos' },
  { key: 'poster', label: 'Posters' },
  { key: 'background', label: 'Backgrounds' },
  { key: 'squareart', label: 'Square Art' },
]

function candidateKey(key: ArtworkListType, c: ArtworkCandidate): string {
  return `${key}:${c.source}:${c.ref}`
}

function candidatesForKey(data: ArtworkCandidatesResponse, key: ArtworkListType): ArtworkCandidate[] {
  return key === 'logo' ? data.logos
    : key === 'poster' ? data.posters
      : key === 'background' ? data.backgrounds
        : data.squareart
}

function previewUrl(key: ArtworkListType, c: ArtworkCandidate): string {
  if (c.source === 'gracenote') return getGracenoteImageProxyUrl(c.ref)
  if (c.source === 'tvdb') return c.ref   // already an absolute artwork URL
  const size = key === 'background' ? 'w780' : 'w300'
  return `https://image.tmdb.org/t/p/${size}${c.ref}`
}

// Checkerboard for logo thumbs — kept on the button (behind the img) so the "make white" CSS
// filter recolors only the logo, not its backing (which would otherwise turn the board white too).
const CHECKER: CSSProperties = {
  backgroundColor: '#aaa',
  backgroundImage:
    'linear-gradient(45deg, #888 25%, transparent 25%), linear-gradient(-45deg, #888 25%, transparent 25%), linear-gradient(45deg, transparent 75%, #888 75%), linear-gradient(-45deg, transparent 75%, #888 75%)',
  backgroundSize: '12px 12px',
  backgroundPosition: '0 0, 0 6px, 6px -6px, -6px 0px',
}

// Full-resolution URL for the lightbox preview.
function fullUrl(c: ArtworkCandidate): string {
  if (c.source === 'gracenote') return getGracenoteImageProxyUrl(c.ref)
  if (c.source === 'tvdb') return c.ref
  return `https://image.tmdb.org/t/p/original${c.ref}`
}

// Same-origin URL for downloading (a proper filename / no CORS), matching the TMDB search page.
function downloadUrl(c: ArtworkCandidate): string {
  if (c.source === 'gracenote') return getGracenoteImageProxyUrl(c.ref)
  if (c.source === 'tvdb') return getTvdbImageProxyUrl(c.ref)
  return getTmdbImageProxyUrl(c.ref)
}

type ArtworkPreview = { src: string; download: string; filename: string; isLogo: boolean; white: boolean; dims: string }

export default function ArtworkFinderCard({ item, syncTargetIndex, scopeLabel, missing, infoExtra }: Props) {
  const { showToast } = useToast()
  const [open, setOpen] = useState(false)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)
  // Candidates are cached per source: TMDB (+ Plex square art) loads with the card, TheTVDB only
  // once its tab is clicked.
  const tvdbEnabled = useTvdbEnabled()
  const overview = useCardOverview(item)
  // TVDB-only items (no TMDB id) open straight on the TVDB tab — TMDB has nothing for them.
  const [source, setSource] = useState<ImageSource>((item.tmdb_id ?? 0) > 0 ? 'tmdb' : 'tvdb')
  const [language, setLanguage] = useState('en+textless')   // TMDB image language preference
  const [dataBySource, setDataBySource] = useState<Partial<Record<ImageSource, ArtworkCandidatesResponse>>>({})
  const data = dataBySource[source] ?? null
  const [makeWhite, setMakeWhite] = useState<Record<string, boolean>>({})   // logo ref -> recolor on save
  const [adding, setAdding] = useState<Record<string, boolean>>({})
  const [added, setAdded] = useState<Record<string, string>>({})            // candidate key -> written filename
  const [preview, setPreview] = useState<ArtworkPreview | null>(null)       // click-to-enlarge lightbox
  const [overwriteConfirm, setOverwriteConfirm] = useState<{ subtype: ArtworkSubtype; candidate: ArtworkCandidate; filename: string } | null>(null)
  const [cropTarget, setCropTarget] = useState<ArtworkCandidate | null>(null)   // image open in the crop modal
  const [cropping, setCropping] = useState(false)
  const [cropOverwrite, setCropOverwrite] = useState<{ candidate: ArtworkCandidate; crop: { x: number; y: number; size: number } } | null>(null)
  const [showLocalPicker, setShowLocalPicker] = useState(false)   // pick artwork from a server folder
  const [showTextLogo, setShowTextLogo] = useState(false)         // render a styled text logo

  // Mirrors the maker card: a chip per source whether or not the item carries that id.
  const idChips = [
    { label: 'TMDB', display: item.tmdb_id ? `#${item.tmdb_id}` : null, copy: String(item.tmdb_id ?? '') },
    { label: 'IMDB', display: item.imdb_id || null, copy: item.imdb_id ?? '' },
    { label: 'TVDB', display: item.tvdb_id ? `#${item.tvdb_id}` : null, copy: String(item.tvdb_id ?? '') },
  ]

  const downloadImage = useCallback(async (url: string, filename: string) => {
    try {
      const resp = await fetch(url)
      if (!resp.ok) throw new Error(`HTTP ${resp.status}`)
      const objectUrl = URL.createObjectURL(await resp.blob())
      const a = document.createElement('a')
      a.href = objectUrl
      a.download = filename
      document.body.appendChild(a)
      a.click()
      a.remove()
      URL.revokeObjectURL(objectUrl)
    } catch {
      showToast('Failed to download image', 'error')
    }
  }, [showToast])

  const openPreview = useCallback((key: ArtworkListType, c: ArtworkCandidate, white: boolean) => {
    setPreview({
      src: fullUrl(c),
      download: downloadUrl(c),
      filename: c.ref.split('/').filter(Boolean).pop() || `${key}.${key === 'logo' ? 'png' : 'jpg'}`,
      isLogo: key === 'logo',
      white,
      dims: c.width && c.height ? `${c.width}×${c.height}` : '',
    })
  }, [])

  const load = useCallback(async (which: ImageSource, lang = language) => {
    setLoading(true)
    setError(null)
    try {
      const result = await getArtworkCandidates(item, undefined, undefined, which, lang)
      setDataBySource((prev) => ({ ...prev, [which]: result }))
    } catch (e) {
      setError(getApiErrorMessage(e, 'Failed to load artwork'))
    } finally {
      setLoading(false)
    }
  }, [item, language])

  const toggle = useCallback(() => {
    setOpen((prev) => {
      const next = !prev
      if (next && !data && !loading) void load(source)
      return next
    })
  }, [data, loading, load, source])

  const handleSourceChange = useCallback((next: ImageSource) => {
    if (next === source) return
    setSource(next)
    setError(null)
    if (!dataBySource[next]) void load(next)
  }, [source, dataBySource, load])

  const handleLanguageChange = useCallback((next: string) => {
    if (next === language) return
    setLanguage(next)
    setDataBySource({})   // every cached listing is language-scoped
    setError(null)
    void load(source, next)
  }, [language, source, load])

  const noScope = syncTargetIndex == null
  // Online browsing needs a TMDB id, or a TVDB id with TheTVDB configured. Custom collections
  // have neither — their card offers only the local-folder picker.
  const canBrowse = (item.tmdb_id ?? 0) > 0 || ((item.tvdb_id ?? 0) > 0 && tvdbEnabled)

  const handleAdd = useCallback(async (subtype: ArtworkSubtype, c: ArtworkCandidate, confirmOverwrite = false) => {
    if (noScope) {
      showToast('Pick an artwork scope first', 'error')
      return
    }
    const key = candidateKey(subtype, c)
    setAdding((a) => ({ ...a, [key]: true }))
    try {
      const res = await addArtwork({
        sync_target_index: syncTargetIndex,
        tmdb_id: item.tmdb_id,
        media_type: item.media_type,
        title: item.title,
        year: item.year || undefined,   // '' would fail the API's int parse
        tvdb_id: item.tvdb_id,
        imdb_id: item.imdb_id,
        subtype,
        source: c.source,
        ref: c.ref,
        make_white: subtype === 'logo' ? !!makeWhite[c.ref] : false,
        confirm_overwrite: confirmOverwrite,
      })
      if (res.status === 'exists') {
        // A file with this name already exists — ask before overwriting.
        setOverwriteConfirm({ subtype, candidate: c, filename: res.written })
        return
      }
      setAdded((a) => ({ ...a, [key]: res.written }))
      showToast(`Added ${res.written}`, 'success')
    } catch (e) {
      showToast(getApiErrorMessage(e, 'Add failed'), 'error')
    } finally {
      setAdding((a) => ({ ...a, [key]: false }))
    }
  }, [noScope, syncTargetIndex, item, makeWhite, showToast])

  const autoPickWhite = useCallback(() => {
    const logos = data?.logos ?? []
    const white = logos.find((c) => c.is_white) ?? [...logos].sort(
      (a, b) => (a.off_white_pct ?? 999) - (b.off_white_pct ?? 999),
    )[0]
    if (!white) {
      showToast('No white logo found', 'error')
      return
    }
    void handleAdd('logo', white)
  }, [data, handleAdd, showToast])

  const doCrop = useCallback(async (c: ArtworkCandidate, crop: { x: number; y: number; size: number }, confirmOverwrite = false) => {
    if (noScope) {
      showToast('Pick an artwork scope first', 'error')
      return
    }
    setCropping(true)
    try {
      const res = await cropArtworkSquare({
        sync_target_index: syncTargetIndex,
        tmdb_id: item.tmdb_id,
        media_type: item.media_type,
        title: item.title,
        year: item.year || undefined,   // '' would fail the API's int parse
        tvdb_id: item.tvdb_id,
        imdb_id: item.imdb_id,
        source: c.source,
        ref: c.ref,
        x: crop.x,
        y: crop.y,
        size: crop.size,
        confirm_overwrite: confirmOverwrite,
      })
      if (res.status === 'exists') {
        setCropOverwrite({ candidate: c, crop })
        return
      }
      showToast(`Saved square art: ${res.written}`, 'success')
      setCropTarget(null)
      setCropOverwrite(null)
    } catch (e) {
      showToast(getApiErrorMessage(e, 'Crop failed'), 'error')
    } finally {
      setCropping(false)
    }
  }, [noScope, syncTargetIndex, item, showToast])

  // Same copy behaviour as the TMDB search card, incl. the execCommand fallback for http LAN
  // instances where navigator.clipboard is unavailable.
  const copyToClipboard = useCallback((text: string) => {
    const doFallback = () => {
      try {
        const el = document.createElement('textarea')
        el.value = text
        el.style.position = 'fixed'
        el.style.left = '-9999px'
        el.style.top = '-9999px'
        document.body.appendChild(el)
        el.focus()
        el.select()
        ;(document as unknown as { execCommand(cmd: string): boolean }).execCommand('copy')
        document.body.removeChild(el)
        showToast('Copied to clipboard', 'success')
      } catch {
        showToast('Failed to copy', 'error')
      }
    }
    if (navigator.clipboard?.writeText) {
      void navigator.clipboard.writeText(text).then(() => showToast('Copied to clipboard', 'success')).catch(doFallback)
    } else {
      doFallback()
    }
  }, [showToast])

  const typeIcon = item.media_type === 'movie' ? <MovieIcon size={12} />
    : item.media_type === 'tv' ? <Tv size={12} /> : <FolderOpen size={12} />

  return (
    <div className="tmdb-result-wrapper artwork-finder-card" style={{ marginBottom: 16 }}>
      <div className="tmdb-result-card">
        <div className="tmdb-poster">
          {item.poster_url
            ? <img src={item.poster_url} alt={item.title} loading="lazy" />
            : <div className="tmdb-poster-placeholder">{item.media_type === 'movie' ? <MovieIcon size={28} /> : item.media_type === 'tv' ? <Tv size={28} /> : <FolderOpen size={28} />}</div>}
        </div>
        <div className="tmdb-result-info">
          <div className="tmdb-result-title-row">
            <span className="tmdb-result-title">{item.title}</span>
            {item.year && <span className="tmdb-result-year">{item.year}</span>}
          </div>

          {overview && <p className="tmdb-result-overview">{overview}</p>}
          {infoExtra}
        </div>

        {/* Same box as the maker card: everything actionable inside, poster + title outside. */}
        <div className="tmdb-result-box">
          <div className="tmdb-result-meta">
            <span className={`badge ${item.media_type === 'movie' ? 'badge-blue' : item.media_type === 'tv' ? 'badge-green' : 'badge-orange'}`}>{typeIcon} {item.media_type === 'movie' ? 'Movie' : item.media_type === 'tv' ? 'Series' : 'Collection'}</span>
            {missing && missing.length > 0 && (
              <>
                <span className="artwork-missing-label">Missing:</span>
                {missing.map((t) => (
                  <span key={t} className="badge artwork-missing-badge">{MISSING_LABEL[t]}</span>
                ))}
              </>
            )}
          </div>

          <ServiceLinks item={item} />

          {/* All three chips always render, matching the maker card. */}
          <div className="tmdb-result-ids tmdb-result-ids--compact">
            {idChips.map(({ label, display, copy }) => (
              display
                ? (
                  <button
                    key={label}
                    type="button"
                    className="tmdb-id-chip"
                    onClick={() => copyToClipboard(copy)}
                    title={`Copy ${label} ID`}
                  >
                    <Copy size={10} />{label}&nbsp;{display}
                  </button>
                )
                : (
                  <span key={label} className="tmdb-id-chip tmdb-id-chip--empty" title={`No ${label} ID`}>
                    No {label} ID
                  </span>
                )
            ))}
          </div>

          <div className="tmdb-result-copyrow">
            <button type="button" className="tmdb-copy-btn" onClick={() => copyToClipboard(item.year ? `${item.title} (${item.year})` : item.title)} title="Copy title with year">
              <Copy size={12} /> Title
            </button>
            {item.homepage && (
              <button type="button" className="tmdb-copy-btn" onClick={() => copyToClipboard(item.homepage)} title="Copy TMDB link">
                <Copy size={12} /> Link
              </button>
            )}
          </div>

          <div className="tmdb-gallery-toggle-row">
            {canBrowse && (
              <button type="button" className="tmdb-gallery-toggle" onClick={toggle} disabled={loading}>
                <ImageIcon size={13} />
                {loading ? 'Loading…' : open ? <><ChevronUp size={13} /> Hide</> : <><ChevronDown size={13} /> Find artwork</>}
              </button>
            )}
            <button
              type="button"
              className="tmdb-gallery-toggle"
              title="Pick a background / square art from a folder on the server"
              onClick={() => noScope ? showToast('Pick an artwork scope first', 'error') : setShowLocalPicker(true)}
            >
              <FolderOpen size={13} /> From folder
            </button>
            <button
              type="button"
              className="tmdb-gallery-toggle"
              title="Render a styled text logo for this title and add it to the scope"
              onClick={() => noScope ? showToast('Pick an artwork scope first', 'error') : setShowTextLogo(true)}
            >
              <TypeIcon size={13} /> Text logo
            </button>
          </div>
        </div>
      </div>

      {open && (
        <div className="tmdb-gallery-panel">
          <div className="tmdb-gallery-tabs" style={{ marginBottom: 10 }}>
            {tvdbEnabled && (
              <div className="tmdb-gallery-sources" role="group" aria-label="Artwork source">
                {([['tmdb', 'TMDB', tmdbIcon], ['tvdb', 'TVDB', tvdbIcon]] as const).map(([id, label, icon]) => (
                  <button
                    key={id}
                    type="button"
                    className={`tmdb-gallery-source tmdb-gallery-source--${id}${source === id ? ' active' : ''}`}
                    onClick={() => handleSourceChange(id)}
                    disabled={loading}
                    title={`Browse ${label} artwork`}
                    aria-label={`Browse ${label} artwork`}
                  >
                    <img className="tmdb-gallery-source-icon" src={icon} alt="" />
                  </button>
                ))}
              </div>
            )}
            {/* Same language preference as the maker card's gallery; applies to the TMDB listings. */}
            <div className="tmdb-gallery-lang-wrapper">
              <Globe size={13} className="tmdb-gallery-lang-icon" />
              <select
                className="tmdb-gallery-lang-select"
                value={language}
                onChange={(e) => handleLanguageChange(e.target.value)}
                disabled={loading}
                title="Image language preference"
              >
                {TMDB_IMAGE_LANGUAGES.map((lang) => (
                  <option key={lang.value} value={lang.value}>{lang.label}</option>
                ))}
              </select>
            </div>
          </div>
          {error && <p className="tmdb-error">{error}</p>}
          {loading && !data && <p style={{ fontSize: '0.8rem', color: '#888', margin: '0 0 10px' }}>Loading {source === 'tmdb' ? 'TMDB' : 'TheTVDB'} artwork…</p>}
          {data && source === 'tmdb' && !data.plex_available && (
            <p style={{ fontSize: '0.8rem', color: '#ffb74d', margin: '0 0 10px' }}>
              No Plex token configured — square art (Plex-only; TMDB has none) is unavailable. Add a Plex instance in Settings to enable it.
            </p>
          )}
          {data && SECTIONS.map(({ key, label }) => {
            const cands = candidatesForKey(data, key)
            const gridClass = key === 'logo' ? 'tmdb-gallery-grid--logos'
              : key === 'poster' ? 'tmdb-gallery-grid--posters' : 'tmdb-gallery-grid--backdrops'
            const canAdd = key !== 'poster'          // posters aren't a scope subtype
            const canCrop = key === 'poster' || key === 'background'  // good square-crop sources
            return (
              <div key={key} style={{ marginBottom: 14 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10, marginBottom: 6 }}>
                  <strong style={{ fontSize: '0.85rem' }}>{label}</strong>
                  <span style={{ fontSize: '0.75rem', color: '#888' }}>{cands.length}</span>
                  {key === 'logo' && cands.length > 0 && (
                    <button type="button" className="btn-toolbar" style={{ fontSize: '0.72rem', padding: '2px 8px', minWidth: 0 }} onClick={autoPickWhite} title="Add the first white logo">
                      <Sparkles size={12} /> Auto-pick white
                    </button>
                  )}
                  {key === 'poster' && cands.length > 0 && (
                    <span style={{ fontSize: '0.72rem', color: '#888' }}>— crop one into square art</span>
                  )}
                </div>
                {cands.length === 0
                  ? <p style={{ fontSize: '0.78rem', color: '#777', margin: 0 }}>
                      {key === 'squareart' && source === 'tvdb'
                        ? "TheTVDB has no square art — switch to the TMDB tab for Plex's square art."
                        : 'None found.'}
                    </p>
                  : (
                    <div className={`tmdb-gallery-grid ${gridClass}`}>
                      {cands.map((c) => {
                        const ckey = candidateKey(key, c)
                        const isAdded = !!added[ckey]
                        const isAdding = !!adding[ckey]
                        const white = key === 'logo' ? !!makeWhite[c.ref] : false
                        return (
                          <div key={ckey} className="tmdb-gallery-item">
                            <div className="tmdb-gallery-thumb-wrapper">
                              <button type="button" className="tmdb-gallery-thumb-btn" style={key === 'logo' ? CHECKER : undefined} onClick={() => openPreview(key, c, white)} title="Click to preview full size">
                                <img
                                  className="tmdb-gallery-thumb"
                                  src={previewUrl(key, c)}
                                  alt=""
                                  loading="lazy"
                                  style={{
                                    ...(key === 'squareart' ? { aspectRatio: '1 / 1' } : {}),
                                    // Logos: keep the checkerboard on the BUTTON (behind the img), and blank the
                                    // img's own — so "make white" recolors only the logo, board stays checkered.
                                    ...(key === 'logo' ? { backgroundColor: 'transparent', backgroundImage: 'none' } : {}),
                                    ...(key === 'logo' && white ? { filter: 'brightness(0) invert(1)' } : {}),
                                  }}
                                />
                              </button>
                              {/* Collection logos are borrowed from member movies — overlay which one. */}
                              {c.origin && (
                                <span className="tmdb-gallery-origin-badge" title={`From ${c.origin}`}>{c.origin}</span>
                              )}
                            </div>
                            <div className="tmdb-gallery-item-meta">
                              <div className="tmdb-gallery-meta-row" style={{ gap: 6 }}>
                                {/* Under the TMDB tab only square art comes from Plex, so its badge is
                                    the one worth showing; the TVDB tab labels its own. */}
                                {c.source === 'gracenote' && <span className="badge badge-grey" style={{ fontSize: '0.62rem' }}>Plex</span>}
                                {c.source === 'tvdb' && <span className="badge badge-grey" style={{ fontSize: '0.62rem' }}>TVDB</span>}
                                {/* TL / language code, same badge the TMDB search gallery uses. Square art is a
                                    single Plex image with no language, so it gets none. */}
                                {key !== 'squareart' && (c.language === null
                                  ? <span className="tmdb-gallery-lang" title="Textless">TL</span>
                                  : c.language
                                    ? <span className="tmdb-gallery-lang" title={`Tagged ${c.language.toUpperCase()} — likely has text`}>{c.language.toUpperCase()}</span>
                                    : null)}
                                {c.width && c.height ? <span className="tmdb-gallery-dims">{c.width}×{c.height}</span> : null}
                                {key === 'logo' && typeof c.off_white_pct === 'number' && (
                                  <span className={`badge ${c.is_white ? 'badge-green' : 'badge-orange'}`} style={{ fontSize: '0.62rem' }} title={c.is_white ? 'Passes the white filter' : 'Off-white %'}>
                                    {c.is_white ? <Check size={10} /> : null} {c.off_white_pct.toFixed(1)}%
                                  </span>
                                )}
                              </div>
                              <div style={{ display: 'flex', gap: 6 }}>
                                {canAdd && (
                                  <button type="button" className={`btn-toolbar ${isAdded ? '' : 'btn-primary'}`} style={{ flex: 1, minWidth: 0, fontSize: '0.72rem', padding: '3px 6px', justifyContent: 'center' }} onClick={() => handleAdd(key as ArtworkSubtype, c)} disabled={isAdding || isAdded}>
                                    {isAdded ? <><Check size={12} /> Added</> : isAdding ? 'Adding…' : <><Plus size={12} /> Add</>}
                                  </button>
                                )}
                                {key === 'logo' && (
                                  <button type="button" className={`btn-toolbar ${white ? 'btn-primary' : ''}`} style={{ minWidth: 0, fontSize: '0.72rem', padding: '3px 6px', justifyContent: 'center' }} title="Recolor to solid white on save" onClick={() => setMakeWhite((m) => ({ ...m, [c.ref]: !m[c.ref] }))}>
                                    <Wand2 size={12} /> White
                                  </button>
                                )}
                                {canCrop && (
                                  <button type="button" className="btn-toolbar" style={{ flex: key === 'poster' ? 1 : undefined, minWidth: 0, fontSize: '0.72rem', padding: '3px 6px', justifyContent: 'center' }} title="Crop this image into square art" onClick={() => noScope ? showToast('Pick an artwork scope first', 'error') : setCropTarget(c)}>
                                    <CropIcon size={12} /> Crop → Square
                                  </button>
                                )}
                              </div>
                            </div>
                          </div>
                        )
                      })}
                    </div>
                  )}
              </div>
            )
          })}
          {noScope && data && (
            <p style={{ fontSize: '0.78rem', color: '#ffb74d', margin: 0 }}>Choose an artwork scope at the top to enable adding.</p>
          )}
          {!noScope && scopeLabel && data && (
            <p style={{ fontSize: '0.75rem', color: '#777', margin: 0 }}>Adds land in scope: <strong>{scopeLabel}</strong> (IDarr renames + uploads on its next run).</p>
          )}
        </div>
      )}

      {/* Full-size preview lightbox — mirrors the TMDB search gallery */}
      {preview && (
        <div className="tmdb-lightbox-overlay" onClick={() => setPreview(null)}>
          <div className="tmdb-gallery-lightbox" onClick={(e) => e.stopPropagation()}>
            {preview.isLogo && preview.white ? (
              // The white filter also whitens an element's own background, so keep the backing on
              // a wrapper (not the filtered img) — dark, so the recolored white logo stays visible.
              <div style={{ ...CHECKER, padding: '1.5rem 2rem', borderRadius: 8, boxShadow: '0 8px 40px rgba(0,0,0,0.7)', display: 'flex', maxWidth: '100%', maxHeight: 'calc(92vh - 3.5rem)' }}>
                <img src={preview.src} alt="Preview" style={{ maxWidth: '100%', maxHeight: '100%', objectFit: 'contain', display: 'block', filter: 'brightness(0) invert(1)' }} />
              </div>
            ) : (
              <img
                className={`tmdb-gallery-lightbox-img${preview.isLogo ? ' tmdb-gallery-lightbox-img--logo' : ''}`}
                src={preview.src}
                alt="Preview"
              />
            )}
            <div className="tmdb-gallery-lightbox-actions">
              {preview.dims && <span className="tmdb-gallery-dims">{preview.dims}</span>}
              <button
                type="button"
                className="btn-toolbar btn-primary"
                style={{ fontSize: '0.82rem', padding: '0.35rem 0.75rem', minWidth: 0 }}
                onClick={() => void downloadImage(preview.download, preview.filename)}
              >
                <Download size={13} /> Download
              </button>
            </div>
          </div>
          <button type="button" className="tmdb-lightbox-close" onClick={() => setPreview(null)}>×</button>
        </div>
      )}

      {/* Overwrite confirmation — mirrors the PSD-export overwrite modal */}
      {overwriteConfirm && (
        <div className="modal-overlay">
          <div className="modal-content schedule-modal">
            <div className="modal-header">
              <h2>Overwrite existing artwork?</h2>
              <button className="modal-close" onClick={() => setOverwriteConfirm(null)}>×</button>
            </div>
            <div className="modal-body">
              <p style={{ color: '#ccc', lineHeight: 1.6, marginBottom: '0.75rem' }}>
                This scope already has a file with this name:
              </p>
              <div className="psd-not-found-filename"><code>{overwriteConfirm.filename}</code></div>
              {scopeLabel && (
                <div className="psd-not-found-folder">
                  <span className="psd-not-found-folder-label">Scope:</span>
                  <code>{scopeLabel}</code>
                </div>
              )}
              <p style={{ marginTop: '1rem', color: '#ffb74d', fontSize: '0.85rem', lineHeight: 1.6 }}>
                Overwriting moves the existing file to the IDarr duplicates folder and saves this one in its place.
              </p>
            </div>
            <div className="modal-footer">
              <button className="btn-secondary" onClick={() => setOverwriteConfirm(null)}>Cancel</button>
              <button
                className="btn-primary"
                style={{ justifyContent: 'center', background: '#f44336' }}
                onClick={() => {
                  const o = overwriteConfirm
                  setOverwriteConfirm(null)
                  void handleAdd(o.subtype, o.candidate, true)
                }}
              >
                Overwrite
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Crop-to-square tool */}
      {cropTarget && (
        <SquareCropModal
          imageUrl={fullUrl(cropTarget)}
          title={item.year ? `${item.title} (${item.year})` : item.title}
          saving={cropping}
          onCancel={() => { setCropTarget(null); setCropOverwrite(null) }}
          onSave={(crop) => void doCrop(cropTarget, crop, false)}
        />
      )}

      {/* Crop overwrite confirmation */}
      {cropOverwrite && (
        <div className="modal-overlay">
          <div className="modal-content schedule-modal">
            <div className="modal-header">
              <h2>Overwrite existing square art?</h2>
              <button className="modal-close" onClick={() => setCropOverwrite(null)}>×</button>
            </div>
            <div className="modal-body">
              <p style={{ color: '#ccc', lineHeight: 1.6 }}>
                This scope already has square art for this title. Overwriting moves the existing file to the
                IDarr duplicates folder and saves the cropped one in its place.
              </p>
              {scopeLabel && (
                <div className="psd-not-found-folder">
                  <span className="psd-not-found-folder-label">Scope:</span>
                  <code>{scopeLabel}</code>
                </div>
              )}
            </div>
            <div className="modal-footer">
              <button className="btn-secondary" onClick={() => setCropOverwrite(null)}>Cancel</button>
              <button
                className="btn-primary"
                style={{ justifyContent: 'center', background: '#f44336' }}
                onClick={() => {
                  const o = cropOverwrite
                  setCropOverwrite(null)
                  void doCrop(o.candidate, o.crop, true)
                }}
              >
                Overwrite
              </button>
            </div>
          </div>
        </div>
      )}

      {/* Pick backgrounds / square art from a server-side folder */}
      {showLocalPicker && syncTargetIndex != null && (
        <LocalArtworkPickerModal
          item={item}
          syncTargetIndex={syncTargetIndex}
          scopeLabel={scopeLabel}
          onClose={() => setShowLocalPicker(false)}
        />
      )}

      {/* Render a styled text logo for this title */}
      {showTextLogo && syncTargetIndex != null && (
        <TextLogoModal
          item={item}
          syncTargetIndex={syncTargetIndex}
          scopeLabel={scopeLabel}
          onClose={() => setShowTextLogo(false)}
        />
      )}
    </div>
  )
}
