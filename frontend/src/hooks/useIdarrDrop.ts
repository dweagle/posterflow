import { useCallback, useEffect, useRef, useState, type DragEvent } from 'react'
import { getApiErrorMessage } from '../api/client'
import { useToast } from '../components/Toast'
import { useIdarrSyncTarget, getSyncTargetLabel } from './useIdarrSyncTarget'
import { quickAddFilesToIdarr } from '../utils/idarrQuickAdd'

export type IdarrDropState = 'idle' | 'adding' | 'done'

interface UseIdarrDropOptions {
  /** Off when the host handles drops itself. */
  enabled?: boolean
  /** Pin the drop to one sync target (artwork cards use their artwork scope); omit for the shared scope. */
  syncTargetIndex?: number | null
}

const isFileDrag = (event: DragEvent) => Array.from(event.dataTransfer?.types ?? []).includes('Files')

/**
 * Makes a card a drop target for IDarr quick add. Spread `dropProps` on the card; render the
 * drop zone when `enabled`. Only real file drags count, so dragging a gallery image around does
 * not light the card up.
 */
export function useIdarrDrop({ enabled = true, syncTargetIndex }: UseIdarrDropOptions = {}) {
  const { targets, selectedIndex } = useIdarrSyncTarget()
  const { showToast } = useToast()
  const [dragOver, setDragOver] = useState(false)
  const [state, setState] = useState<IdarrDropState>('idle')
  const depthRef = useRef(0)
  const resetTimerRef = useRef<number | null>(null)

  const targetIndex = syncTargetIndex === undefined ? selectedIndex : syncTargetIndex
  const active = enabled && targetIndex !== null && targetIndex >= 0 && targetIndex < targets.length
  const targetLabel = active ? getSyncTargetLabel(targets[targetIndex as number], targetIndex as number) : ''

  useEffect(() => () => {
    if (resetTimerRef.current !== null) window.clearTimeout(resetTimerRef.current)
  }, [])

  const addFiles = useCallback(async (files: File[]) => {
    if (!active || state === 'adding') return
    setState('adding')
    try {
      const result = await quickAddFilesToIdarr(targetIndex as number, files)
      const skipped = result.skippedCount > 0 ? `, ${result.skippedCount} skipped` : ''
      showToast(`IDarr (${targetLabel}): added ${result.uploadedCount} file(s)${skipped}`, 'success')
      if (result.autoRenameError) {
        showToast(getApiErrorMessage(result.autoRenameError, 'Files added, but failed to start IDarr auto-rename'), 'error')
      }
      setState('done')
      resetTimerRef.current = window.setTimeout(() => setState('idle'), 3000)
    } catch (error) {
      showToast(getApiErrorMessage(error, 'Failed to add files to IDarr'), 'error')
      setState('idle')
    }
  }, [active, state, targetIndex, targetLabel, showToast])

  const onDragEnter = useCallback((event: DragEvent) => {
    if (!active || !isFileDrag(event)) return
    event.preventDefault()
    depthRef.current += 1
    setDragOver(true)
  }, [active])

  const onDragOver = useCallback((event: DragEvent) => {
    if (!active || !isFileDrag(event)) return
    event.preventDefault()
    event.dataTransfer.dropEffect = 'copy'
  }, [active])

  const onDragLeave = useCallback((event: DragEvent) => {
    if (!active || !isFileDrag(event)) return
    depthRef.current = Math.max(0, depthRef.current - 1)
    if (depthRef.current === 0) setDragOver(false)
  }, [active])

  const onDrop = useCallback((event: DragEvent) => {
    if (!active) return
    event.preventDefault()
    event.stopPropagation()
    depthRef.current = 0
    setDragOver(false)
    const files = Array.from(event.dataTransfer?.files ?? [])
    if (files.length) void addFiles(files)
  }, [active, addFiles])

  return {
    enabled: active,
    dragOver,
    state,
    targetLabel,
    dropProps: active ? { onDragEnter, onDragOver, onDragLeave, onDrop } : {},
  }
}
