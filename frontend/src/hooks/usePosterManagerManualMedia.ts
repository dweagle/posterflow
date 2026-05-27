import { useCallback, useEffect, useState } from 'react'
import { ManualMediaEntry, addManualMedia, deleteManualMedia, listManualMedia } from '../api/posterManager'

type ToastType = 'success' | 'error' | 'info'

interface UsePosterManagerManualMediaParams {
  showToast: (message: string, type?: ToastType) => void
}

export const usePosterManagerManualMedia = ({ showToast }: UsePosterManagerManualMediaParams) => {
  const [manualEntries, setManualEntries] = useState<ManualMediaEntry[]>([])
  const [formTitle, setFormTitle] = useState('')
  const [formYear, setFormYear] = useState('')
  const [formType, setFormType] = useState<'movie' | 'series'>('movie')
  const [formSeasonCount, setFormSeasonCount] = useState('')
  const [formSpecials, setFormSpecials] = useState(false)
  const [formTmdbId, setFormTmdbId] = useState('')
  const [formTvdbId, setFormTvdbId] = useState('')
  const [formImdbId, setFormImdbId] = useState('')
  const [adding, setAdding] = useState(false)
  const [loadingEntries, setLoadingEntries] = useState(false)

  const fetchManualEntries = useCallback(async () => {
    setLoadingEntries(true)
    try {
      const data = await listManualMedia()
      setManualEntries(data.entries)
    } catch {
      // silently ignore on load
    } finally {
      setLoadingEntries(false)
    }
  }, [])

  useEffect(() => {
    fetchManualEntries()
  }, [fetchManualEntries])

  const handleAddManualEntry = async () => {
    const title = formTitle.trim()
    if (!title) {
      showToast('Title is required', 'error')
      return
    }
    const year = formYear.trim() ? parseInt(formYear.trim(), 10) : null
    const seasonsCount =
      formType === 'series' && formSeasonCount.trim() ? parseInt(formSeasonCount.trim(), 10) : null

    setAdding(true)
    try {
      const result = await addManualMedia({
        title,
        year,
        media_type: formType,
        seasons_count: seasonsCount,
        include_specials: formType === 'series' ? formSpecials : false,
        tmdb_id: formTmdbId.trim() ? parseInt(formTmdbId.trim(), 10) : null,
        tvdb_id: formTvdbId.trim() ? parseInt(formTvdbId.trim(), 10) : null,
        imdb_id: formImdbId.trim() || null,
      })
      setManualEntries((prev) => [...prev, result.entry])
      const seasonNote =
        formType === 'series' && result.entry.seasons.length > 0
          ? ` (${result.entry.seasons.length} seasons)`
          : ''
      showToast(`Added "${result.entry.title}"${seasonNote}`, 'success')
      setFormTitle('')
      setFormYear('')
      setFormSeasonCount('')
      setFormSpecials(false)
      setFormTmdbId('')
      setFormTvdbId('')
      setFormImdbId('')
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : 'Failed to add entry'
      showToast(msg, 'error')
    } finally {
      setAdding(false)
    }
  }

  const handleDeleteEntry = async (id: number) => {
    const entry = manualEntries.find((e) => e.id === id)
    try {
      await deleteManualMedia(id)
      setManualEntries((prev) => prev.filter((e) => e.id !== id))
      showToast(`Removed "${entry?.title ?? 'entry'}"`, 'success')
    } catch {
      showToast('Failed to remove entry', 'error')
    }
  }

  return {
    manualEntries,
    formTitle,
    formYear,
    formType,
    formSeasonCount,
    formSpecials,
    formTmdbId,
    formTvdbId,
    formImdbId,
    adding,
    loadingEntries,
    setFormTitle,
    setFormYear,
    setFormType,
    setFormSeasonCount,
    setFormSpecials,
    setFormTmdbId,
    setFormTvdbId,
    setFormImdbId,
    handleAddManualEntry,
    handleDeleteEntry,
  }
}
