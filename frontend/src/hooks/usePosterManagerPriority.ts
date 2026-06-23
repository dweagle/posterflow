import { MutableRefObject, useEffect, useRef, useState } from 'react'
import { Drive, getDrivePriority, saveDrivePriority } from '../api/client'

type ToastType = 'success' | 'error' | 'info'

interface UsePosterManagerPriorityParams {
  drives: Drive[]
  originalPriorityRef: MutableRefObject<{ drive_ids: number[]; enabled_styles: string[] } | null>
  setHasUnsavedPriorityChanges: (value: boolean) => void
  showToast: (message: string, type?: ToastType) => void
}

// ============================================================================
// Touch drag support (self-contained).
//
// Desktop uses the native HTML5 drag-and-drop handlers below and is completely
// untouched by anything in this section. Touchscreens (iPad/phone) fire none of
// those drag events, so this parallel path reconstructs dragging from raw touch
// events: it hit-tests with elementFromPoint, floats a clone under the finger,
// auto-scrolls near the edges, and finally commits using the same reorder math
// the native path uses. It reuses only the shared priorityList / draggedDrive /
// dragOverIndex state — nothing in the native handlers is modified.
// ============================================================================

type TouchDrop =
  | { type: 'priority'; index: number }
  | { type: 'available' }
  | { type: 'none' }

const TOUCH_DRAG_THRESHOLD = 6 // px of finger travel before a press becomes a drag
const TOUCH_EDGE_ZONE = 56 // px from a scroll container edge that triggers auto-scroll
const TOUCH_EDGE_SPEED = 14 // px per frame auto-scroll speed

interface TouchDragState {
  touchId: number
  drive: Drive
  sourceEl: HTMLElement
  startX: number
  startY: number
  lastY: number
  offsetX: number
  offsetY: number
  started: boolean
  ghost: HTMLElement | null
  scrollEl: HTMLElement | null
}

const findTouch = (touches: TouchList, id: number): Touch | null => {
  for (let i = 0; i < touches.length; i++) {
    if (touches[i].identifier === id) return touches[i]
  }
  return null
}

// Where a touch at (x, y) would land, using the element under the finger.
const resolveTouchDrop = (el: HTMLElement | null, y: number, listLength: number): TouchDrop => {
  if (!el) return { type: 'none' }
  if (el.closest('.available-drives')) return { type: 'available' }
  if (el.closest('.priority-drop-zone')) {
    const cardEl = el.closest('[data-priority-index]') as HTMLElement | null
    if (cardEl) {
      const index = Number(cardEl.dataset.priorityIndex)
      const rect = cardEl.getBoundingClientRect()
      return { type: 'priority', index: y > rect.top + rect.height / 2 ? index + 1 : index }
    }
    if (el.closest('.drop-zone-start')) return { type: 'priority', index: 0 }
    return { type: 'priority', index: listLength }
  }
  return { type: 'none' }
}

const createTouchGhost = (source: HTMLElement, x: number, y: number) => {
  const rect = source.getBoundingClientRect()
  const offsetX = x - rect.left
  const offsetY = y - rect.top
  const ghost = source.cloneNode(true) as HTMLElement
  ghost.classList.add('drag-ghost')
  ghost.classList.remove('drop-target-before', 'drop-target-after')
  ghost.style.position = 'fixed'
  ghost.style.left = '0'
  ghost.style.top = '0'
  ghost.style.width = `${rect.width}px`
  ghost.style.height = `${rect.height}px`
  ghost.style.margin = '0'
  ghost.style.pointerEvents = 'none'
  ghost.style.zIndex = '9999'
  ghost.style.transform = `translate3d(${x - offsetX}px, ${y - offsetY}px, 0)`
  document.body.appendChild(ghost)
  return { ghost, offsetX, offsetY }
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

  // --- Touch drag controller (see header note; desktop is unaffected) ---

  // Refs let the window-level touch listeners read fresh state without re-subscribing.
  const priorityListRef = useRef<Drive[]>(priorityList)
  priorityListRef.current = priorityList
  const touchDragRef = useRef<TouchDragState | null>(null)
  const touchAutoScrollRaf = useRef<number | null>(null)
  // Last indicator value pushed to React, so we only re-render on an actual change.
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

  // Remove listeners/ghost/auto-scroll and clear the body flags (no React state).
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

    // Stop the page/list from scrolling while a drag is in progress.
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

  // Entry point wired to every drive card. The drag only starts from the grip
  // handle so the rest of the card (and the surrounding list) can still scroll.
  const handleDriveTouchStart = useRef((e: React.TouchEvent<HTMLElement>, drive: Drive) => {
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

  // Safety net: drop any in-flight touch drag if the component unmounts mid-gesture.
  useEffect(() => () => teardownTouchDrag(), [teardownTouchDrag])

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
    handleDriveTouchStart,
    savePriority,
    resetPriorityToOriginal,
    clearDragOverTimeout,
  }
}
