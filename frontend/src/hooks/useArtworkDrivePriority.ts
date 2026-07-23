import { useEffect, useRef, useState } from 'react'
import { ArtworkDrive, getArtworkDrivePriority, saveArtworkDrivePriority } from '../api/client'
import {
  TOUCH_DRAG_THRESHOLD,
  TOUCH_EDGE_SPEED,
  TOUCH_EDGE_ZONE,
  TouchDragState,
  createTouchGhost,
  findTouch,
  resolveTouchDrop,
} from '../utils/touchDragList'

type ToastType = 'success' | 'error' | 'info'

interface UseArtworkDrivePriorityParams {
  drives: ArtworkDrive[]
  showToast: (message: string, type?: ToastType) => void
}

// Parallel to usePosterManagerPriority, minus the poster style scheme (no MM2K/CL2K).
// Reuses the exact same drag/drop + touch machinery and CSS selectors so the asset
// priority tab behaves identically to the poster one. hasUnsavedChanges is managed
// internally so the tab can be self-contained.

export const useArtworkDrivePriority = ({ drives, showToast }: UseArtworkDrivePriorityParams) => {
  const [priorityList, setPriorityList] = useState<ArtworkDrive[]>([])
  const [draggedDrive, setDraggedDrive] = useState<ArtworkDrive | null>(null)
  const [dragOverIndex, setDragOverIndex] = useState<number | null>(null)
  const [hasUnsavedChanges, setHasUnsavedChanges] = useState(false)
  const originalRef = useRef<number[] | null>(null)
  const dragOverTimeoutRef = useRef<number | null>(null)

  const clearDragOverTimeout = () => {
    if (dragOverTimeoutRef.current) {
      clearTimeout(dragOverTimeoutRef.current)
      dragOverTimeoutRef.current = null
    }
  }

  // Recompute unsaved state whenever the order changes vs. the last saved order.
  useEffect(() => {
    if (originalRef.current === null) return
    const current = priorityList.map(d => d.id)
    const original = originalRef.current
    const changed = current.length !== original.length || current.some((id, i) => id !== original[i])
    setHasUnsavedChanges(changed)
  }, [priorityList])

  const loadPriority = async () => {
    try {
      const priority = await getArtworkDrivePriority()
      const ids = Array.isArray(priority.drive_ids) ? priority.drive_ids : []
      const ordered: ArtworkDrive[] = []
      for (const id of ids) {
        const drive = drives.find(d => d.id === id)
        if (drive) ordered.push(drive)
      }
      originalRef.current = ordered.map(d => d.id)
      setPriorityList(ordered)
      setHasUnsavedChanges(false)
    } catch (error) {
      console.error('Error loading asset priority:', error)
    }
  }

  const handleDragStart = (drive: ArtworkDrive) => {
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
    if (sourceIndex === 0) {
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
    const rect = (e.currentTarget as HTMLElement).getBoundingClientRect()
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
    if (relatedTarget && relatedTarget.closest('.priority-drop-zone')) return
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
    if (sourceIndex !== -1 && sourceIndex < rawTargetIndex) insertionIndex -= 1
    insertionIndex = Math.max(0, Math.min(insertionIndex, filtered.length))
    filtered.splice(insertionIndex, 0, draggedDrive)
    setPriorityList(filtered)
    setDragOverIndex(null)
    setDraggedDrive(null)
  }

  const handleRemoveFromPriority = (driveId: number) => {
    setPriorityList(prev => prev.filter(d => d.id !== driveId))
  }

  const handleAddAllGroup = (group: 'preset' | 'custom') => {
    const toAdd = drives.filter(d => {
      const matches = group === 'custom' ? d.is_custom : !d.is_custom
      return matches && d.subscribed && !priorityList.find(p => p.id === d.id)
    })
    if (toAdd.length === 0) return
    setPriorityList(prev => [...prev, ...toAdd])
  }

  const handleRemoveAllGroup = (group: 'preset' | 'custom') => {
    setPriorityList(prev => prev.filter(d => (group === 'custom' ? !d.is_custom : d.is_custom)))
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
      const ids = priorityList.map(d => d.id)
      await saveArtworkDrivePriority(ids)
      originalRef.current = [...ids]
      setHasUnsavedChanges(false)
      showToast('Artwork priority saved successfully!')
    } catch (error) {
      console.error('Error saving asset priority:', error)
      showToast('Failed to save asset priority', 'error')
    }
  }

  // --- Touch drag controller (mirrors the poster hook; desktop is unaffected) ---
  const priorityListRef = useRef<ArtworkDrive[]>(priorityList)
  priorityListRef.current = priorityList
  const touchDragRef = useRef<TouchDragState<ArtworkDrive> | null>(null)
  const touchAutoScrollRaf = useRef<number | null>(null)
  const touchIndicatorRef = useRef<number | null>(null)

  const touchAutoScrollTick = useRef(() => {
    const st = touchDragRef.current
    if (!st || !st.started) {
      touchAutoScrollRaf.current = null
      return
    }
    const container = st.scrollEl
    if (container) {
      const rect = container.getBoundingClientRect()
      if (st.lastY < rect.top + TOUCH_EDGE_ZONE && container.scrollTop > 0) {
        container.scrollTop -= TOUCH_EDGE_SPEED
      } else if (st.lastY > rect.bottom - TOUCH_EDGE_ZONE) {
        container.scrollTop += TOUCH_EDGE_SPEED
      }
    }
    touchAutoScrollRaf.current = requestAnimationFrame(touchAutoScrollTick)
  }).current

  const teardownTouchDrag = useRef(() => {
    window.removeEventListener('touchmove', onTouchMove)
    window.removeEventListener('touchend', onTouchEnd)
    window.removeEventListener('touchcancel', onTouchCancel)
    const st = touchDragRef.current
    if (st?.ghost) st.ghost.remove()
    st?.sourceEl.classList.remove('touch-source-dragging')
    if (touchAutoScrollRaf.current != null) {
      cancelAnimationFrame(touchAutoScrollRaf.current)
      touchAutoScrollRaf.current = null
    }
    document.body.classList.remove('touch-dragging')
    touchDragRef.current = null
  }).current

  const endTouchDrag = useRef(() => {
    teardownTouchDrag()
    touchIndicatorRef.current = null
    setDraggedDrive(null)
    setDragOverIndex(null)
  }).current

  const onTouchMove = useRef((e: TouchEvent) => {
    const st = touchDragRef.current
    if (!st) return
    const touch = findTouch(e.changedTouches, st.touchId)
    if (!touch) return
    st.lastY = touch.clientY

    if (!st.started) {
      if (Math.hypot(touch.clientX - st.startX, touch.clientY - st.startY) < TOUCH_DRAG_THRESHOLD) return
      st.started = true
      const { ghost, offsetX, offsetY } = createTouchGhost(st.sourceEl, touch.clientX, touch.clientY)
      st.ghost = ghost
      st.offsetX = offsetX
      st.offsetY = offsetY
      st.sourceEl.classList.add('touch-source-dragging')
      document.body.classList.add('touch-dragging')
      setDraggedDrive(st.drive)
      if (touchAutoScrollRaf.current == null) touchAutoScrollRaf.current = requestAnimationFrame(touchAutoScrollTick)
    }

    if (e.cancelable) e.preventDefault()

    if (st.ghost) {
      st.ghost.style.transform = `translate3d(${touch.clientX - st.offsetX}px, ${touch.clientY - st.offsetY}px, 0)`
    }

    const under = document.elementFromPoint(touch.clientX, touch.clientY) as HTMLElement | null
    st.scrollEl = (under?.closest('.priority-drop-zone, .available-drives-scroll') as HTMLElement | null) ?? null

    const drop = resolveTouchDrop(under, touch.clientY, priorityListRef.current.length)
    let nextIndicator: number | null = null
    if (drop.type === 'priority') {
      const list = priorityListRef.current
      const sourceIndex = list.findIndex(d => d.id === st.drive.id)
      const isNoOp = sourceIndex !== -1 && (drop.index === sourceIndex || drop.index === sourceIndex + 1)
      nextIndicator = isNoOp ? null : drop.index
    }
    if (nextIndicator !== touchIndicatorRef.current) {
      touchIndicatorRef.current = nextIndicator
      setDragOverIndex(nextIndicator)
    }
  }).current

  const onTouchEnd = useRef((e: TouchEvent) => {
    const st = touchDragRef.current
    if (!st) return
    const touch = findTouch(e.changedTouches, st.touchId)
    if (!touch) return

    if (st.started) {
      const under = document.elementFromPoint(touch.clientX, touch.clientY) as HTMLElement | null
      const drop = resolveTouchDrop(under, touch.clientY, priorityListRef.current.length)
      if (drop.type === 'available') {
        setPriorityList(prev => prev.filter(d => d.id !== st.drive.id))
      } else if (drop.type === 'priority') {
        setPriorityList(prev => {
          const sourceIndex = prev.findIndex(d => d.id === st.drive.id)
          const filtered = prev.filter(d => d.id !== st.drive.id)
          let insertion = drop.index
          if (sourceIndex !== -1 && sourceIndex < drop.index) insertion -= 1
          insertion = Math.max(0, Math.min(insertion, filtered.length))
          filtered.splice(insertion, 0, st.drive)
          return filtered
        })
      }
    }
    endTouchDrag()
  }).current

  const onTouchCancel = useRef((e: TouchEvent) => {
    const st = touchDragRef.current
    if (!st || !findTouch(e.changedTouches, st.touchId)) return
    endTouchDrag()
  }).current

  const handleDriveTouchStart = useRef((e: React.TouchEvent<HTMLElement>, drive: ArtworkDrive) => {
    const target = e.target as HTMLElement
    if (!target.closest('.drag-handle') || target.closest('.btn-remove')) return
    const touch = e.changedTouches[0]
    if (!touch) return

    touchIndicatorRef.current = null
    touchDragRef.current = {
      touchId: touch.identifier,
      drive,
      sourceEl: e.currentTarget,
      startX: touch.clientX,
      startY: touch.clientY,
      lastY: touch.clientY,
      offsetX: 0,
      offsetY: 0,
      started: false,
      ghost: null,
      scrollEl: null,
    }
    window.addEventListener('touchmove', onTouchMove, { passive: false })
    window.addEventListener('touchend', onTouchEnd)
    window.addEventListener('touchcancel', onTouchCancel)
  }).current

  useEffect(() => () => teardownTouchDrag(), [teardownTouchDrag])

  return {
    priorityList,
    draggedDrive,
    dragOverIndex,
    hasUnsavedChanges,
    loadPriority,
    handleDragStart,
    handleDragOver,
    handleDragOverStart,
    handleDragEnd,
    handleDragOverCard,
    handleDragLeave,
    handleDropInPriority,
    handleRemoveFromPriority,
    handleAddAllGroup,
    handleRemoveAllGroup,
    handleDropInAvailable,
    handleDragOverEnd,
    handleDriveTouchStart,
    savePriority,
  }
}
