import { useState } from 'react'
import {
  createSchedule,
  deleteSchedule,
  Drive,
  Schedule,
  updateSchedule,
} from '../api/client'

interface UseSettingsSchedulesParams {
  schedules: Schedule[]
  setSchedules: React.Dispatch<React.SetStateAction<Schedule[]>>
  drives: Drive[]
  fetchSchedules: () => Promise<void>
  showToast: (message: string, type?: 'success' | 'error' | 'info') => void
}

export const useSettingsSchedules = ({
  schedules,
  setSchedules,
  drives,
  fetchSchedules,
  showToast,
}: UseSettingsSchedulesParams) => {
  const normalizeTimeToken = (value: string): string | null => {
    const match = value.trim().match(/^(\d{1,2}):(\d{1,2})$/)
    if (!match) {
      return null
    }

    const hours = Number(match[1])
    const minutes = Number(match[2])
    if (!Number.isInteger(hours) || !Number.isInteger(minutes) || hours < 0 || hours > 23 || minutes < 0 || minutes > 59) {
      return null
    }

    return `${String(hours).padStart(2, '0')}:${String(minutes).padStart(2, '0')}`
  }

  const normalizeCommaSeparatedTimes = (value: string): string | null => {
    const tokens = value.split(',').map((token) => token.trim())
    if (tokens.length === 0 || tokens.some((token) => token === '')) {
      return null
    }

    const normalizedTokens: string[] = []
    for (const token of tokens) {
      const normalized = normalizeTimeToken(token)
      if (!normalized) {
        return null
      }
      normalizedTokens.push(normalized)
    }

    return normalizedTokens.join(',')
  }

  const isSyncJobType = (jobType: string) => jobType === 'gdrive_sync' || jobType === 'sync'
  const isIdarrJobType = (jobType: string) => jobType === 'idarr'

  const getTaskLabel = (jobType: string) => {
    switch (jobType) {
      case 'gdrive_sync':
      case 'sync':
        return 'Drive Sync'
      case 'poster_workflow':
        return 'Poster Workflow'
      case 'poster_renamer':
        return 'Poster Renamer'
      case 'idarr':
        return 'IDarr'
      case 'maker_monitor':
        return 'Maker Monitor'
      case 'plex_upload':
        return 'Plex Upload'
      case 'unmatched_assets':
        return 'Unmatched Assets'
      case 'border_replacer':
        return 'Border Replacer'
      default:
        return 'Unknown Task'
    }
  }

  const [editingSchedule, setEditingSchedule] = useState<{ schedule: Schedule; index: number } | null>(null)
  const [scheduleValueCache, setScheduleValueCache] = useState<Record<string, string>>({})
  const [scheduleSaving, setScheduleSaving] = useState(false)

  const addSchedule = () => {
    const tempId = -Date.now()
    const newSchedule: Schedule = {
      id: tempId,
      job_type: 'gdrive_sync',
      name: 'New Schedule',
      enabled: true,
      drive_id: null,
      drive_group: null,
      schedule_type: 'daily',
      schedule_value: '',
      last_run: null,
      next_run: null,
      job_config: null,
    }
    setSchedules([...schedules, newSchedule])
    setEditingSchedule({ schedule: { ...newSchedule }, index: schedules.length })
  }

  const updateScheduleField = <K extends keyof Schedule>(field: K, value: Schedule[K]) => {
    if (field === 'job_type') {
      const nextJobType = value as string
      const shouldClearDriveTargeting = !isSyncJobType(nextJobType) && !isIdarrJobType(nextJobType)

      setEditingSchedule((previous) => {
        if (!previous) return previous
        return {
          ...previous,
          schedule: {
            ...previous.schedule,
            job_type: nextJobType,
            drive_id: shouldClearDriveTargeting ? null : previous.schedule.drive_id,
            drive_group: shouldClearDriveTargeting ? null : previous.schedule.drive_group,
          },
        }
      })
      return
    }

    if (field === 'schedule_type') {
      const nextScheduleType = value as Schedule['schedule_type']

      setEditingSchedule((previous) => {
        if (!previous) return previous
        if (nextScheduleType === previous.schedule.schedule_type) {
          return previous
        }

        const currentType = previous.schedule.schedule_type
        const currentValue = previous.schedule.schedule_value
        if (currentValue) {
          setScheduleValueCache(prev => ({
            ...prev,
            [currentType]: currentValue,
          }))
        }

        const restoredValue = scheduleValueCache[nextScheduleType] || ''
        return {
          ...previous,
          schedule: {
            ...previous.schedule,
            schedule_type: nextScheduleType,
            schedule_value: restoredValue,
          },
        }
      })
      return
    }

    setEditingSchedule((previous) => {
      if (!previous) return previous
      return {
        ...previous,
        schedule: {
          ...previous.schedule,
          [field]: value,
        },
      }
    })
  }

  const toggleEditSchedule = (index: number) => {
    const scheduleToEdit = { ...schedules[index] }

    if (isSyncJobType(scheduleToEdit.job_type) && scheduleToEdit.drive_id !== null) {
      const driveExists = drives.some((d) => d.id === scheduleToEdit.drive_id)
      if (!driveExists) {
        scheduleToEdit.drive_id = null
      }
    }

    setEditingSchedule({ schedule: scheduleToEdit, index })
  }

  const toggleScheduleEnabled = async (index: number) => {
    const schedule = schedules[index]
    const updated = { ...schedule, enabled: !schedule.enabled }

    if (schedule.id < 0) {
      const nextSchedules = [...schedules]
      nextSchedules[index] = updated
      setSchedules(nextSchedules)
      return
    }

    try {
      await updateSchedule(schedule.id, updated)
      await fetchSchedules()
      showToast(`Schedule ${updated.enabled ? 'enabled' : 'disabled'}`)
    } catch (error) {
      console.error('Error toggling schedule:', error)
      showToast('Failed to update schedule', 'error')
    }
  }

  const saveSchedule = async () => {
    if (!editingSchedule) return
    const schedule = { ...editingSchedule.schedule }

    if (isSyncJobType(schedule.job_type) && schedule.drive_id !== null) {
      const driveExists = drives.some((d) => d.id === schedule.drive_id)
      if (!driveExists) {
        schedule.drive_id = null
      }
    }

    if (!schedule.name || schedule.name.trim() === '') {
      showToast('Please enter a schedule name', 'error')
      return
    }

    switch (schedule.schedule_type) {
      case 'hourly': {
        if (!schedule.schedule_value && schedule.schedule_value !== '0') {
          showToast('Please enter a minute value (0-59)', 'error')
          return
        }
        const minute = parseInt(schedule.schedule_value)
        if (isNaN(minute) || minute < 0 || minute > 59) {
          showToast('Minute must be between 0 and 59', 'error')
          return
        }
        break
      }

      case 'daily': {
        const normalizedDailyTime = normalizeTimeToken(schedule.schedule_value || '')
        if (!normalizedDailyTime) {
          showToast('Please enter a valid time in HH:MM format (e.g., 09:00)', 'error')
          return
        }
        schedule.schedule_value = normalizedDailyTime
        break
      }

      case 'multiple_daily': {
        if (!schedule.schedule_value) {
          showToast('Please enter at least one time', 'error')
          return
        }

        const normalizedDailyTimes = normalizeCommaSeparatedTimes(schedule.schedule_value)
        if (!normalizedDailyTimes) {
          showToast('All times must be in HH:MM format (e.g., 07:00,19:00)', 'error')
          return
        }

        schedule.schedule_value = normalizedDailyTimes
        break
      }

      case 'weekly': {
        if (!schedule.schedule_value || !schedule.schedule_value.includes(':')) {
          showToast('Please select a day and enter a time', 'error')
          return
        }
        const weeklyParts = schedule.schedule_value.split(':')
        const dayPart = weeklyParts[0]?.trim()
        const weeklyTime = weeklyParts.slice(1).join(':').trim()
        const weeklyDay = Number(dayPart)
        if (!Number.isInteger(weeklyDay) || weeklyDay < 0 || weeklyDay > 6) {
          showToast('Please select a valid day', 'error')
          return
        }
        if (!weeklyTime) {
          showToast('Please enter at least one time', 'error')
          return
        }

        const normalizedWeeklyTimes = normalizeCommaSeparatedTimes(weeklyTime)
        if (!normalizedWeeklyTimes) {
          showToast('All times must be in HH:MM format (e.g., 09:00,14:30)', 'error')
          return
        }

        schedule.schedule_value = `${weeklyDay}:${normalizedWeeklyTimes}`
        break
      }

      case 'multiple_days': {
        if (!schedule.schedule_value || !schedule.schedule_value.includes(':')) {
          showToast('Please select at least one day and enter times', 'error')
          return
        }
        const daySchedules = schedule.schedule_value.split('|').filter(s => s)
        if (daySchedules.length === 0) {
          showToast('Please select at least one day', 'error')
          return
        }

        const normalizedDaySchedules: string[] = []

        for (const daySched of daySchedules) {
          const separatorIndex = daySched.indexOf(':')
          if (separatorIndex <= 0) {
            showToast('Please enter times for all selected days', 'error')
            return
          }

          const dayPart = daySched.slice(0, separatorIndex).trim()
          const times = daySched.slice(separatorIndex + 1).trim()
          const dayIndex = Number(dayPart)
          if (!Number.isInteger(dayIndex) || dayIndex < 0 || dayIndex > 6) {
            showToast('Please select valid days', 'error')
            return
          }

          if (!times || times.trim() === '') {
            showToast('Please enter times for all selected days', 'error')
            return
          }

          const normalizedTimeList = normalizeCommaSeparatedTimes(times)
          if (!normalizedTimeList) {
            showToast('All times must be in HH:MM format', 'error')
            return
          }

          normalizedDaySchedules.push(`${dayIndex}:${normalizedTimeList}`)
        }

        schedule.schedule_value = normalizedDaySchedules.join('|')
        break
      }

      case 'monthly': {
        if (!schedule.schedule_value || !schedule.schedule_value.includes(':')) {
          showToast('Please enter a day of month and time', 'error')
          return
        }
        const monthlyParts = schedule.schedule_value.split(':')
        const dayOfMonth = monthlyParts[0]
        const monthlyTime = monthlyParts.slice(1).join(':').trim()
        const day = parseInt(dayOfMonth)
        if (!day || day < 1 || day > 31) {
          showToast('Day of month must be between 1 and 31', 'error')
          return
        }
        if (!monthlyTime) {
          showToast('Please enter at least one time', 'error')
          return
        }

        const normalizedMonthlyTimes = normalizeCommaSeparatedTimes(monthlyTime)
        if (!normalizedMonthlyTimes) {
          showToast('All times must be in HH:MM format (e.g., 09:00,14:30)', 'error')
          return
        }

        schedule.schedule_value = `${day}:${normalizedMonthlyTimes}`
        break
      }

      case 'cron': {
        if (!schedule.schedule_value || schedule.schedule_value.trim() === '') {
          showToast('Please enter a cron expression', 'error')
          return
        }
        const cronParts = schedule.schedule_value.trim().split(/\s+/)
        if (cronParts.length !== 5) {
          showToast('Cron expression must have 5 parts: minute hour day month weekday', 'error')
          return
        }
        break
      }

      default:
        showToast('Please select a schedule type', 'error')
        return
    }

    setScheduleSaving(true)
    try {
      if (schedule.id < 0) {
        await createSchedule(schedule)
      } else {
        await updateSchedule(schedule.id, schedule)
      }
      await fetchSchedules()
      setEditingSchedule(null)
      setScheduleValueCache({})
      showToast('Schedule saved successfully')
    } catch (error) {
      console.error('Error saving schedule:', error)
      showToast('Failed to save schedule', 'error')
    } finally {
      setScheduleSaving(false)
    }
  }

  const removeSchedule = async (index: number) => {
    const schedule = schedules[index]
    if (schedule.id < 0) {
      setSchedules(schedules.filter((_, i) => i !== index))
      if (editingSchedule && editingSchedule.index === index) {
        setEditingSchedule(null)
      }
    } else {
      try {
        await deleteSchedule(schedule.id)
        await fetchSchedules()
        showToast('Schedule deleted successfully')
      } catch (error) {
        console.error('Error deleting schedule:', error)
        showToast('Failed to delete schedule', 'error')
      }
    }
  }

  const cancelScheduleEdit = () => {
    if (!editingSchedule) {
      return
    }

    const current = editingSchedule
    if (current.schedule.id < 0) {
      setSchedules(schedules.filter((_, i) => i !== current.index))
    }

    setEditingSchedule(null)
    setScheduleValueCache({})
  }

  const getScheduleSummary = (schedule: Schedule): JSX.Element => {
    if (!schedule.enabled) return <span className="summary-disabled">Disabled</span>

    const taskLabel = getTaskLabel(schedule.job_type)
    const isSyncSchedule = isSyncJobType(schedule.job_type)

    let driveInfo = 'All Subscribed'
    if (isSyncSchedule && schedule.drive_id) {
      const drive = drives.find(d => d.id === schedule.drive_id)
      driveInfo = drive ? drive.name : 'All Subscribed'
    } else if (isSyncSchedule && schedule.drive_group) {
      driveInfo = `All ${schedule.drive_group}`
    }

    const summaryScope = isSyncSchedule ? `${taskLabel} · ${driveInfo}` : taskLabel

    switch (schedule.schedule_type) {
      case 'hourly': {
        const minute = schedule.schedule_value || '0'
        const paddedMinute = minute.padStart(2, '0')
        return (
          <>
            <span className="summary-type">{summaryScope} · Hourly</span>
            <div className="summary-days">
              <span className="summary-day-chip">Every hour at <strong>:{paddedMinute}</strong></span>
            </div>
          </>
        )
      }
      case 'daily':
        return (
          <>
            <span className="summary-type">{summaryScope} · Daily</span>
            <div className="summary-days">
              <span className="summary-day-chip">at <strong>{schedule.schedule_value}</strong></span>
            </div>
          </>
        )
      case 'multiple_daily': {
        if (!schedule.schedule_value) return <span className="summary-disabled">Not configured</span>
        const times = schedule.schedule_value.split(',').map(t => t.trim())
        return (
          <>
            <span className="summary-type">{summaryScope} · Daily</span>
            <div className="summary-days">
              {times.map((time, i) => (
                <span key={i} className="summary-day-chip">
                  <strong>{time}</strong>
                </span>
              ))}
            </div>
          </>
        )
      }
      case 'weekly': {
        if (!schedule.schedule_value) return <span className="summary-disabled">Not configured</span>
        const weeklyParts = schedule.schedule_value.split(':')
        const day = weeklyParts[0]
        const times = weeklyParts.slice(1).join(':')
        const dayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
        const timeList = times.split(',').map(t => t.trim()).filter(t => t)
        return (
          <>
            <span className="summary-type">{summaryScope} · Weekly</span>
            <div className="summary-days">
              <span className="summary-day-chip">
                {dayNames[parseInt(day)]}: <strong>{timeList.join(', ')}</strong>
              </span>
            </div>
          </>
        )
      }
      case 'multiple_days': {
        if (!schedule.schedule_value) return <span className="summary-disabled">Not configured</span>
        const pairs = schedule.schedule_value.split('|').map(p => p.trim()).filter(p => p)
        const dayNames = ['Sun', 'Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat']
        return (
          <>
            <span className="summary-type">{summaryScope} · Multiple Days</span>
            <div className="summary-days">
              {pairs.map((pair, i) => {
                const parts = pair.split(':')
                const day = parts[0]
                const times = parts.slice(1).join(':')
                const timeList = times ? times.split(',').map(t => t.trim()).filter(t => t) : []
                return (
                  <span key={i} className="summary-day-chip">
                    {dayNames[parseInt(day)]}: <strong>{timeList.join(', ') || '—'}</strong>
                  </span>
                )
              })}
            </div>
          </>
        )
      }
      case 'monthly': {
        if (!schedule.schedule_value) return <span className="summary-disabled">Not configured</span>
        const monthlyParts = schedule.schedule_value.split(':')
        const dayOfMonth = monthlyParts[0]
        const times = monthlyParts.slice(1).join(':')
        const timeList = times.split(',').map(t => t.trim()).filter(t => t)
        return (
          <>
            <span className="summary-type">{summaryScope} · Monthly</span>
            <div className="summary-days">
              <span className="summary-day-chip">
                Day {dayOfMonth}: <strong>{timeList.join(', ')}</strong>
              </span>
            </div>
          </>
        )
      }
      case 'cron':
        return (
          <>
            <span className="summary-type">{summaryScope} · Custom Cron</span>
            <div className="summary-days">
              <code className="summary-cron">{schedule.schedule_value}</code>
            </div>
          </>
        )
      default:
        return <span className="summary-disabled">Not configured</span>
    }
  }

  return {
    editingSchedule,
    scheduleSaving,
    addSchedule,
    updateScheduleField,
    toggleEditSchedule,
    toggleScheduleEnabled,
    saveSchedule,
    removeSchedule,
    cancelScheduleEdit,
    getScheduleSummary,
    setEditingSchedule,
  }
}
