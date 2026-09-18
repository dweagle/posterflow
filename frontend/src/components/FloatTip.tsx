import { useCallback, useEffect, useRef } from 'react'
import { createPortal } from 'react-dom'
import './FloatTip.css'

const SHOW_DELAY_MS = 600
const MARGIN = 8
const GAP = 6

/**
 * Delayed, mouse-only hover tip rendered on <body> at a fixed position that is clamped to the
 * viewport, so no scroll container can clip it and no nearby edge can push it off-screen.
 * Right-aligned with its trigger and shown below it when that fits, else above.
 */
export function useFloatTip(delayMs = SHOW_DELAY_MS) {
  const elRef = useRef<HTMLDivElement | null>(null)
  const timer = useRef<number | null>(null)

  const hide = useCallback(() => {
    if (timer.current) {
      window.clearTimeout(timer.current)
      timer.current = null
    }
    if (elRef.current) elRef.current.style.display = 'none'
  }, [])

  const show = useCallback((e: React.PointerEvent<HTMLElement>, text: string) => {
    if (e.pointerType !== 'mouse') return
    const rect = e.currentTarget.getBoundingClientRect()
    if (timer.current) window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => {
      const el = elRef.current
      if (!el) return
      el.textContent = text
      el.style.display = 'block'
      const { offsetWidth: w, offsetHeight: h } = el
      const fitsBelow = rect.bottom + GAP + h <= window.innerHeight - MARGIN
      const top = fitsBelow ? rect.bottom + GAP : rect.top - GAP - h
      const left = rect.right - w
      el.style.top = `${Math.max(MARGIN, Math.min(top, window.innerHeight - h - MARGIN))}px`
      el.style.left = `${Math.max(MARGIN, Math.min(left, window.innerWidth - w - MARGIN))}px`
    }, delayMs)
  }, [delayMs])

  useEffect(() => hide, [hide])

  const tip = createPortal(<div className="float-tip" ref={elRef} style={{ display: 'none' }} />, document.body)
  return { show, hide, tip }
}
