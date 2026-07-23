// Shared touch drag-and-drop plumbing for the priority lists (poster + artwork drives).
// The hooks own the state machine; this holds the DOM helpers and geometry they share.

export type TouchDrop =
  | { type: 'priority'; index: number }
  | { type: 'available' }
  | { type: 'none' }

export const TOUCH_DRAG_THRESHOLD = 6 // px of finger travel before a press becomes a drag
export const TOUCH_EDGE_ZONE = 56 // px from a scroll container edge that triggers auto-scroll
export const TOUCH_EDGE_SPEED = 14 // px per frame auto-scroll speed

export interface TouchDragState<TItem> {
  touchId: number
  drive: TItem
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

export const findTouch = (touches: TouchList, id: number): Touch | null => {
  for (let i = 0; i < touches.length; i++) {
    if (touches[i].identifier === id) return touches[i]
  }
  return null
}

// Where a touch at (x, y) would land, using the element under the finger.
export const resolveTouchDrop = (el: HTMLElement | null, y: number, listLength: number): TouchDrop => {
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

export const createTouchGhost = (source: HTMLElement, x: number, y: number) => {
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
