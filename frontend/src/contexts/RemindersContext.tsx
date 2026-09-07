import { createContext, useCallback, useContext, useEffect, useMemo, useState, type ReactNode } from 'react'
import {
  deletePosterReminder,
  getPosterReminders,
  reminderInput,
  reminderMatchesItem,
  savePosterReminder,
  updatePosterReminderNote,
  type PosterReminder,
  type ReminderItem,
  type ReminderKind,
} from '../api/client'

interface RemindersContextType {
  reminders: PosterReminder[]
  loaded: boolean
  error: string | null
  refresh: () => Promise<void>
  find: (kind: ReminderKind, item: ReminderItem) => PosterReminder | undefined
  save: (kind: ReminderKind, item: ReminderItem, note: string) => Promise<PosterReminder>
  updateNote: (id: number, note: string) => Promise<PosterReminder>
  remove: (id: number) => Promise<void>
}

const RemindersContext = createContext<RemindersContextType | null>(null)

/** Shared reminder list for one page: every card's bell and the Reminders tab read the same state,
 *  so flagging on a card shows up in the tab (and unflagging in the tab clears the card) instantly. */
export function RemindersProvider({ children }: { children: ReactNode }) {
  const [reminders, setReminders] = useState<PosterReminder[]>([])
  const [loaded, setLoaded] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const refresh = useCallback(async () => {
    try {
      setReminders(await getPosterReminders())
      setError(null)
    } catch {
      setError('Could not load reminders')
    } finally {
      setLoaded(true)
    }
  }, [])

  useEffect(() => { void refresh() }, [refresh])

  const find = useCallback(
    (kind: ReminderKind, item: ReminderItem) => reminders.find((r) => reminderMatchesItem(r, kind, item)),
    [reminders],
  )

  const upsertLocal = (saved: PosterReminder) => {
    setReminders((prev) => {
      const idx = prev.findIndex((r) => r.id === saved.id)
      if (idx === -1) return [saved, ...prev]
      const next = prev.slice()
      next[idx] = saved
      return next
    })
  }

  const save = useCallback(async (kind: ReminderKind, item: ReminderItem, note: string) => {
    const saved = await savePosterReminder(reminderInput(kind, item, note))
    upsertLocal(saved)
    return saved
  }, [])

  const updateNote = useCallback(async (id: number, note: string) => {
    const saved = await updatePosterReminderNote(id, note)
    upsertLocal(saved)
    return saved
  }, [])

  const remove = useCallback(async (id: number) => {
    await deletePosterReminder(id)
    setReminders((prev) => prev.filter((r) => r.id !== id))
  }, [])

  const value = useMemo(
    () => ({ reminders, loaded, error, refresh, find, save, updateNote, remove }),
    [reminders, loaded, error, refresh, find, save, updateNote, remove],
  )

  return <RemindersContext.Provider value={value}>{children}</RemindersContext.Provider>
}

/** Null outside a provider — cards hosted elsewhere (e.g. community requests) simply hide the bell. */
export function useReminders(): RemindersContextType | null {
  return useContext(RemindersContext)
}
