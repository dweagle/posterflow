import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, renderHook } from '@testing-library/react'
import { usePosterManagerLifecycle } from '../../src/hooks/usePosterManagerLifecycle'

/**
 * A deep link can pin the Unmatched sub-tab through location.state — the Dashboard's
 * "View Details" link opens the poster scope, since the Dashboard shows poster coverage.
 * Without this the link honoured whichever asset type the view was last left on.
 */

// The hook fires several fetches on mount; stub every callback so only the
// location.state effect is under test.
function options(locationState: unknown, extra: Record<string, unknown> = {}) {
  const noopAsync = vi.fn(() => Promise.resolve())
  const ref = { current: null }
  return {
    locationState,
    activeTab: 'flow',
    setActiveTab: vi.fn(),
    setUnmatchedScope: vi.fn(),
    config: null,
    originalConfigRef: ref,
    setHasUnsavedChanges: vi.fn(),
    enabledStyles: new Set(),
    priorityList: [],
    originalPriorityRef: ref,
    setHasUnsavedPriorityChanges: vi.fn(),
    borderColors: [],
    borderWidth: 0,
    bandWidth: 0,
    borderMode: 'full',
    autoRunBorder: false,
    autoRunCleanup: false,
    cleanupDeleteUnknown: false,
    holidaySchedules: [],
    removeBorders: false,
    seasonMode: 'inherit',
    seasonColors: [],
    seasonWidth: 0,
    seasonStyle: 'solid',
    borderStyle: 'solid',
    overlayImage: '',
    overlayRemoveExisting: false,
    gradientColors: [],
    gradientDirection: 'horizontal',
    innerEffect: 'none',
    innerColor: '',
    innerOpacity: 0,
    innerWidth: 0,
    fadeWidth: 0,
    plexRules: [],
    ruleRunTypes: [],
    ruleLibraries: new Set(),
    originalBorderSettingsRef: ref,
    setHasUnsavedBorderChanges: vi.fn(),
    selectedLibraries: new Set(),
    originalLibrarySelectionRef: ref,
    setHasUnsavedLibraryChanges: vi.fn(),
    clearDragOverTimeout: vi.fn(),
    refreshStats: noopAsync,
    fetchConfig: noopAsync,
    fetchDrives: noopAsync,
    fetchFlowConfig: noopAsync,
    fetchIdarrSyncTargets: noopAsync,
    fetchBorderSettings: noopAsync,
    checkActiveWorkflow: noopAsync,
    fetchLibraryConfigs: noopAsync,
    drives: [],
    ...extra,
  }
}

function run(locationState: unknown, extra: Record<string, unknown> = {}) {
  const opts = options(locationState, extra)
  const view = renderHook(() => usePosterManagerLifecycle(opts as never))
  return { opts, view }
}

describe('usePosterManagerLifecycle — deep-link navigation', () => {
  afterEach(() => {
    cleanup()
  })

  it('applies both the active tab and the unmatched scope from location.state', () => {
    const { opts } = run({ activeTab: 'unmatched', unmatchedScope: 'posters' })

    expect(opts.setActiveTab).toHaveBeenCalledWith('unmatched')
    expect(opts.setUnmatchedScope).toHaveBeenCalledWith('posters')
  })

  it('pins an artwork scope when asked', () => {
    const { opts } = run({ activeTab: 'unmatched', unmatchedScope: 'logo' })

    expect(opts.setUnmatchedScope).toHaveBeenCalledWith('logo')
  })

  it('leaves the scope alone when the link carries only a tab', () => {
    const { opts } = run({ activeTab: 'unmatched' })

    expect(opts.setActiveTab).toHaveBeenCalledWith('unmatched')
    expect(opts.setUnmatchedScope).not.toHaveBeenCalled()
  })

  it('does nothing without navigation state', () => {
    const { opts } = run(null)

    expect(opts.setActiveTab).not.toHaveBeenCalled()
    expect(opts.setUnmatchedScope).not.toHaveBeenCalled()
  })

  // The bug this guards: PosterManager rebuilds chooseUnmatchedScope every render, so the
  // effect's deps change and it re-runs on every render. Without the once-per-navigation
  // guard it re-applied the deep-linked scope and bounced the user back to it whenever they
  // clicked another sub-tab. The handlers here are fresh closures each render to reproduce
  // that — a stable spy would leave the deps unchanged and never re-run the effect.
  it('applies the payload once even as it re-runs every render (same location.state)', () => {
    const locationState = { activeTab: 'unmatched', unmatchedScope: 'posters' }
    const scopeSpy = vi.fn()
    const tabSpy = vi.fn()
    const { rerender } = renderHook(() =>
      usePosterManagerLifecycle(
        options(locationState, {
          setUnmatchedScope: (scope: unknown) => scopeSpy(scope),
          setActiveTab: (tab: unknown) => tabSpy(tab),
        }) as never,
      ),
    )

    rerender()
    rerender()

    expect(scopeSpy).toHaveBeenCalledTimes(1)
    expect(tabSpy).toHaveBeenCalledTimes(1)
  })

  it('applies again when a new navigation supplies fresh location.state', () => {
    // Same hook instance, but each navigation hands React Router a new state object.
    const state1: unknown = { activeTab: 'unmatched', unmatchedScope: 'posters' }
    const state2: unknown = { activeTab: 'unmatched', unmatchedScope: 'logo' }
    const setUnmatchedScope = vi.fn()
    let current = state1
    const { rerender } = renderHook(() =>
      usePosterManagerLifecycle(options(current, { setUnmatchedScope }) as never),
    )

    current = state2
    rerender()

    expect(setUnmatchedScope).toHaveBeenNthCalledWith(1, 'posters')
    expect(setUnmatchedScope).toHaveBeenNthCalledWith(2, 'logo')
  })
})
