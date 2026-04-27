import { MutableRefObject, useRef, useState } from 'react'
import { Drive, getDrivePriority, saveDrivePriority } from '../api/client'

type ToastType = 'success' | 'error' | 'info'

interface UsePosterManagerPriorityParams {
  drives: Drive[]
  originalPriorityRef: MutableRefObject<{ drive_ids: number[]; enabled_styles: string[] } | null>
  setHasUnsavedPriorityChanges: (value: boolean) => void
  showToast: (message: string, type?: ToastType) => void
}

export const usePosterManagerPriority = ({
  drives,
  originalPriorityRef,
  setHasUnsavedPriorityChanges,
  showToast,
}: UsePosterManagerPriorityParams) => {
  const [enabledStyles, setEnabledStyles] = useState<Set<string>>(() => {
    const saved = localStorage.getItem('posterManagerEnabledStyles')
    if (saved) {
      try {
        return new Set(JSON.parse(saved))
      } catch {
        return new Set(['MM2K', 'CL2K', 'Custom'])
      }
    }
    return new Set(['MM2K', 'CL2K', 'Custom'])
  })

  const [priorityList, setPriorityList] = useState<Drive[]>([])
  const [draggedDrive, setDraggedDrive] = useState<Drive | null>(null)
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null)
  const dragOverTimeoutRef = useRef<number | null>(null)

  const clearDragOverTimeout = () => {
    if (dragOverTimeoutRef.current) {
      clearTimeout(dragOverTimeoutRef.current)
      dragOverTimeoutRef.current = null
    }
  }

  const loadPriority = async () => {
    try {
      const priority = await getDrivePriority()

      const normalizedEnabledStyles =
        Array.isArray(priority.enabled_styles) && priority.enabled_styles.length > 0
          ? priority.enabled_styles
          : ['MM2K', 'CL2K', 'Custom']

      const configuredDriveIds = Array.isArray(priority.drive_ids) ? priority.drive_ids : []
      const orderedDrives: Drive[] = []

      for (const driveId of configuredDriveIds) {
        const drive = drives.find(d => d.id === driveId)
        if (drive) {
          orderedDrives.push(drive)
        }
      }

      const normalizedDriveIds = orderedDrives.map(drive => drive.id)

      originalPriorityRef.current = {
        drive_ids: normalizedDriveIds,
        enabled_styles: normalizedEnabledStyles,
      }

      setPriorityList(orderedDrives)
      setEnabledStyles(new Set(normalizedEnabledStyles))
      localStorage.setItem('posterManagerEnabledStyles', JSON.stringify(normalizedEnabledStyles))
    } catch (error) {
      console.error('Error loading priority:', error)
    }
  }

  const resetPriorityToOriginal = () => {
    if (!originalPriorityRef.current) return

    const restoredDrives: Drive[] = []
    for (const driveId of originalPriorityRef.current.drive_ids) {
      const drive = drives.find(d => d.id === driveId)
      if (drive) {
        restoredDrives.push(drive)
      }
    }

    setPriorityList(restoredDrives)
    setEnabledStyles(new Set(originalPriorityRef.current.enabled_styles))
    localStorage.setItem('posterManagerEnabledStyles', JSON.stringify(originalPriorityRef.current.enabled_styles))
    setHasUnsavedPriorityChanges(false)
    setDragOverIndex(null)
    setDraggedDrive(null)
  }

  const toggleStyle = (style: string) => {
    setEnabledStyles(prev => {
      const next = new Set(prev)
      if (next.has(style)) {
        next.delete(style)
      } else {
        next.add(style)
      }
      localStorage.setItem('posterManagerEnabledStyles', JSON.stringify(Array.from(next)))
      return next
    })
  }

  const handleDragStart = (drive: Drive) => {
    setDraggedDrive(drive)
    setDragOverIndex(null)
  }

  const handleDragOver = (e: React.DragEvent) => {
    e.preventDefault()
  }

  const handleDragOverStart = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()

    if (!draggedDrive) return

    const sourceIndex = priorityList.findIndex((d) => d.id === draggedDrive.id)
    const isNoOpReorder = sourceIndex === 0

    if (isNoOpReorder) {
      setDragOverIndex(null)
      return
    }

    setDragOverIndex(0)
  }

  const handleDragEnd = () => {
    clearDragOverTimeout()
    setDragOverIndex(null)
    setDraggedDrive(null)
  }

  const handleDragOverCard = (e: React.DragEvent, index: number) => {
    e.preventDefault()
    e.stopPropagation()

    if (!draggedDrive) return

    const targetElement = e.currentTarget as HTMLElement
    const rect = targetElement.getBoundingClientRect()
    const midpointY = rect.top + rect.height / 2
    const insertIndex = e.clientY > midpointY ? index + 1 : index

    const sourceIndex = priorityList.findIndex((d) => d.id === draggedDrive.id)
    const isNoOpReorder = sourceIndex !== -1 && (insertIndex === sourceIndex || insertIndex === sourceIndex + 1)

    if (isNoOpReorder) {
      setDragOverIndex(null)
      return
    }

    setDragOverIndex(insertIndex)
  }

  const handleDragLeave = (e: React.DragEvent) => {
    e.preventDefault()
    const relatedTarget = e.relatedTarget as HTMLElement | null

    if (relatedTarget && relatedTarget.closest('.priority-drop-zone')) {
      return
    }

    if (relatedTarget) {
      clearDragOverTimeout()
      setDragOverIndex(null)
    }
  }

  const handleDropInPriority = (e: React.DragEvent, targetIndex?: number) => {
    e.preventDefault()
    e.stopPropagation()

    if (!draggedDrive) return

    clearDragOverTimeout()

    const sourceIndex = priorityList.findIndex((d) => d.id === draggedDrive.id)
    const filtered = priorityList.filter(d => d.id !== draggedDrive.id)
    const rawTargetIndex = targetIndex ?? dragOverIndex ?? filtered.length
    let insertionIndex = rawTargetIndex

    if (sourceIndex !== -1 && sourceIndex < rawTargetIndex) {
      insertionIndex -= 1
    }

    insertionIndex = Math.max(0, Math.min(insertionIndex, filtered.length))

    filtered.splice(insertionIndex, 0, draggedDrive)

    setPriorityList(filtered)
    setDragOverIndex(null)
    setDraggedDrive(null)
  }

  const handleRemoveFromPriority = (driveId: number) => {
    setPriorityList(prev => prev.filter(d => d.id !== driveId))
  }

  const handleAddAllStyle = (style: 'MM2K' | 'CL2K' | 'Custom') => {
    const toAdd = drives.filter(d => {
      const matchesStyle = style === 'Custom' ? d.is_custom : d.style_type === style
      return matchesStyle && !priorityList.find(p => p.id === d.id)
    })
    if (toAdd.length === 0) return
    setPriorityList(prev => [...prev, ...toAdd])
  }

  const handleRemoveAllStyle = (style: 'MM2K' | 'CL2K' | 'Custom') => {
    setPriorityList(prev => prev.filter(d => {
      const matchesStyle = style === 'Custom' ? d.is_custom : d.style_type === style
      return !matchesStyle
    }))
  }

  const handleDropInAvailable = (e: React.DragEvent) => {
    e.preventDefault()
    clearDragOverTimeout()
    setDragOverIndex(null)
    if (!draggedDrive) return

    setPriorityList(prev => prev.filter(d => d.id !== draggedDrive.id))
    setDraggedDrive(null)
  }

  const handleDragOverEnd = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()

    if (!draggedDrive) return

    const endIndex = priorityList.length
    const sourceIndex = priorityList.findIndex((d) => d.id === draggedDrive.id)
    const isNoOpReorder = sourceIndex !== -1 && (endIndex === sourceIndex || endIndex === sourceIndex + 1)

    if (isNoOpReorder) {
      setDragOverIndex(null)
      return
    }

    setDragOverIndex(endIndex)
  }

  const savePriority = async () => {
    try {
      const priority = {
        drive_ids: priorityList.map(d => d.id),
        enabled_styles: Array.from(enabledStyles),
      }
      await saveDrivePriority(priority)
      originalPriorityRef.current = {
        drive_ids: [...priority.drive_ids],
        enabled_styles: [...priority.enabled_styles],
      }
      setHasUnsavedPriorityChanges(false)
      showToast('Priority saved successfully!')
    } catch (error) {
      console.error('Error saving priority:', error)
      showToast('Failed to save priority', 'error')
    }
  }

  return {
    enabledStyles,
    priorityList,
    draggedDrive,
    dragOverIndex,
    loadPriority,
    toggleStyle,
    handleDragStart,
    handleDragOver,
    handleDragOverStart,
    handleDragEnd,
    handleDragOverCard,
    handleDragLeave,
    handleDropInPriority,
    handleRemoveFromPriority,
    handleAddAllStyle,
    handleRemoveAllStyle,
    handleDropInAvailable,
    handleDragOverEnd,
    savePriority,
    resetPriorityToOriginal,
    clearDragOverTimeout,
  }
}
