import { afterEach, describe, expect, it } from 'vitest'
import { mergeSidebarConfigs, readCachedSidebarConfigs } from '../../src/components/Sidebar'

afterEach(() => localStorage.clear())

describe('sidebar layout cache', () => {
  it('paints the cached order first and appends items the cache never saw', () => {
    localStorage.setItem('posterflow.sidebar.config', JSON.stringify([
      { id: 'dashboard', visible: true, order: 0 },
      { id: 'community-requests', visible: true, order: 1 },
      { id: 'logs', visible: false, order: 2 },
      { id: 'retired-item', visible: true, order: 3 },
    ]))
    const configs = readCachedSidebarConfigs()
    expect(configs.slice(0, 3).map((c) => c.id)).toEqual(['dashboard', 'community-requests', 'logs'])
    expect(configs.find((c) => c.id === 'logs')?.visible).toBe(false)
    expect(configs.some((c) => c.id === 'retired-item')).toBe(false)
    const rest = configs.slice(3)
    expect(rest.map((c) => c.id)).toEqual(['poster-manager', 'drives', 'poster-search', 'plex-upload', 'overlays', 'tools', 'idarr', 'maker-tools'])
    expect(rest.every((c, i) => c.visible && c.order === 4 + i)).toBe(true)
  })

  it('falls back to the default order without a usable cache', () => {
    expect(readCachedSidebarConfigs().map((c) => c.id)[0]).toBe('dashboard')
    localStorage.setItem('posterflow.sidebar.config', 'not json')
    expect(readCachedSidebarConfigs().length).toBe(11)
    localStorage.setItem('posterflow.sidebar.config', JSON.stringify([{ id: 'dashboard' }]))
    expect(readCachedSidebarConfigs()).toEqual(mergeSidebarConfigs([]))
  })
})
