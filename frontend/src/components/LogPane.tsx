import { useRef, useEffect, useLayoutEffect, useCallback, useImperativeHandle, type ReactNode, type Ref } from 'react'
import './LogPane.css'

// Plain-DOM log list. No virtualization, and deliberately NO content-visibility on rows.
// Ordinary divs keep scrollHeight exact and synchronous — stick-to-bottom is a plain
// scrollTop write with none of the estimate-correction races a virtualizer brings.

export type LogPaneHandle = {
  jumpToLatest: () => void
  scrollToItem: (key: string | number) => void
}

type LogPaneProps<T> = {
  items: readonly T[]
  itemKey: (item: T, index: number) => string | number
  renderItem: (item: T, index: number) => ReactNode
  // Stick-to-bottom: glued to the tail until a real user gesture scrolls away.
  follow?: boolean
  // Non-follow panes: start scrolled to the newest entry when content first appears.
  initialBottom?: boolean
  onPinnedChange?: (pinned: boolean) => void
  // Fires when scrolled near the top (history paging). Caller guards re-entry.
  onStartReached?: () => void
  // Fires when scrolled near the bottom (window extension). Caller guards re-entry.
  onEndReached?: () => void
  header?: ReactNode
  footer?: ReactNode
  className?: string
  ref?: Ref<LogPaneHandle>
}

const PIN_THRESHOLD_PX = 160
const TOP_REACH_PX = 80
const END_REACH_PX = 160
const GESTURE_WINDOW_MS = 500

function LogPane<T>({ items, itemKey, renderItem, follow = false, initialBottom = false, onPinnedChange, onStartReached, onEndReached, header, footer, className, ref }: LogPaneProps<T>) {
  const scrollerRef = useRef<HTMLDivElement | null>(null)
  const pinnedRef = useRef(follow)
  const gestureAtRef = useRef(0)
  const pointerDownRef = useRef(false)
  const prevRef = useRef<{ firstKey?: string | number; lastKey?: string | number; scrollHeight: number } | null>(null)

  const onPinnedChangeRef = useRef(onPinnedChange)
  onPinnedChangeRef.current = onPinnedChange
  const onStartReachedRef = useRef(onStartReached)
  onStartReachedRef.current = onStartReached
  const onEndReachedRef = useRef(onEndReached)
  onEndReachedRef.current = onEndReached

  const setPinned = useCallback((value: boolean) => {
    if (pinnedRef.current === value) return
    pinnedRef.current = value
    onPinnedChangeRef.current?.(value)
  }, [])

  const settleTokenRef = useRef(0)
  const glue = useCallback(() => {
    const el = scrollerRef.current
    if (!el) return
    el.scrollTop = el.scrollHeight
    // Late reflow (font load, wrap changes) can land the write a hair short of the
    // true bottom; re-check for a few frames.
    const token = ++settleTokenRef.current
    let tries = 12
    const settle = () => {
      if (token !== settleTokenRef.current) return
      if (follow && !pinnedRef.current) return
      const userScrolling = pointerDownRef.current || performance.now() - gestureAtRef.current < GESTURE_WINDOW_MS
      if (!userScrolling && el.scrollHeight - el.scrollTop - el.clientHeight > 1) {
        el.scrollTop = el.scrollHeight
      }
      if (--tries > 0) requestAnimationFrame(settle)
    }
    requestAnimationFrame(settle)
  }, [follow])

  // Remounts reset the internal pin; tell the parent so button state can't go stale.
  useEffect(() => {
    onPinnedChangeRef.current?.(pinnedRef.current)
  }, [])

  // Real user gestures are the only thing allowed to detach the follow
  // (mousedown covers scrollbar grabs; a held thumb counts until mouseup).
  useEffect(() => {
    const el = scrollerRef.current
    if (!el || !follow) return
    const markGesture = () => { gestureAtRef.current = performance.now() }
    const markPointerDown = () => {
      pointerDownRef.current = true
      gestureAtRef.current = performance.now()
    }
    const clearPointer = () => { pointerDownRef.current = false }
    el.addEventListener('wheel', markGesture, { passive: true })
    el.addEventListener('touchmove', markGesture, { passive: true })
    el.addEventListener('keydown', markGesture)
    el.addEventListener('mousedown', markPointerDown, { passive: true })
    window.addEventListener('mouseup', clearPointer)
    return () => {
      el.removeEventListener('wheel', markGesture)
      el.removeEventListener('touchmove', markGesture)
      el.removeEventListener('keydown', markGesture)
      el.removeEventListener('mousedown', markPointerDown)
      window.removeEventListener('mouseup', clearPointer)
    }
  }, [follow])

  const handleScroll = () => {
    const el = scrollerRef.current
    if (!el) return
    if (follow) {
      const distFromBottom = el.scrollHeight - el.scrollTop - el.clientHeight
      if (distFromBottom <= PIN_THRESHOLD_PX) {
        setPinned(true)
      } else if (pointerDownRef.current || performance.now() - gestureAtRef.current < GESTURE_WINDOW_MS) {
        setPinned(false)
      } else if (pinnedRef.current) {
        // Displaced without a gesture (trim/anchor shift) — stay glued.
        glue()
      }
    }
    if (onStartReachedRef.current && el.scrollTop < TOP_REACH_PX) onStartReachedRef.current()
    if (onEndReachedRef.current && el.scrollHeight - el.scrollTop - el.clientHeight < END_REACH_PX) onEndReachedRef.current()
  }

  // Scroll bookkeeping after every commit: glue while pinned, land at the tail when
  // content first appears, and hold the reading position across history prepends.
  useLayoutEffect(() => {
    const el = scrollerRef.current
    if (!el) return
    const prev = prevRef.current
    const first = items.length > 0 ? itemKey(items[0], 0) : undefined
    const last = items.length > 0 ? itemKey(items[items.length - 1], items.length - 1) : undefined
    const contentJustAppeared = (!prev || prev.lastKey === undefined) && last !== undefined

    if (follow ? pinnedRef.current : (initialBottom && contentJustAppeared)) {
      glue()
    } else if (prev && prev.lastKey !== undefined && last === prev.lastKey && first !== prev.firstKey) {
      el.scrollTop += el.scrollHeight - prev.scrollHeight
    }
    prevRef.current = { firstKey: first, lastKey: last, scrollHeight: el.scrollHeight }
  })

  useImperativeHandle(ref, () => ({
    jumpToLatest: () => {
      setPinned(true)
      glue()
    },
    scrollToItem: (key: string | number) => {
      const row = scrollerRef.current?.querySelector(`[data-key="${key}"]`)
      if (row && typeof row.scrollIntoView === 'function') row.scrollIntoView({ block: 'center' })
    },
  }), [setPinned, glue])

  return (
    <div
      ref={scrollerRef}
      className={`log-pane${follow || onStartReached ? ' log-pane-managed' : ''}${className ? ` ${className}` : ''}`}
      onScroll={handleScroll}
      tabIndex={-1}
    >
      {header}
      {items.map((item, index) => {
        const key = itemKey(item, index)
        return (
          <div key={key} data-key={key} className="log-pane-row">
            {renderItem(item, index)}
          </div>
        )
      })}
      {footer}
    </div>
  )
}

export default LogPane
