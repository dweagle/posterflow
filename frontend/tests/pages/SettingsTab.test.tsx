import { afterEach, describe, expect, it, vi } from 'vitest'
import { cleanup, render, screen } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import SettingsTab from '../../src/components/poster-manager/SettingsTab'
import type { PosterConfig } from '../../src/api/client'

/**
 * The destination help text explains a shared setting: posters and artwork are renamed
 * into the same folder. The path example must track what the rename services actually
 * write (poster_renamer.py / artwork_rename.py), so it stays trustworthy.
 */

const config = (overrides: Partial<PosterConfig> = {}): PosterConfig =>
  ({
    destination: '/kometa/config/assets',
    action_type: 'copy',
    asset_folders: true,
    ...overrides,
  }) as PosterConfig

function renderTab(cfg: PosterConfig = config()) {
  const onConfigChange = vi.fn()
  render(
    <SettingsTab
      config={cfg}
      hasUnsavedChanges={false}
      saving={false}
      onSaveConfig={vi.fn()}
      onConfigChange={onConfigChange}
    />,
  )
  return { onConfigChange }
}

describe('SettingsTab destination help', () => {
  afterEach(() => {
    cleanup()
  })

  it('names every asset type that lands in the destination', () => {
    renderTab()

    const help = screen.getByText(/single folder PosterFlow renames into/i)
    expect(help.textContent).toMatch(/posters, logos, backdrops and square art/i)
    expect(help.textContent).toMatch(/asset_directory/)
  })

  it('warns about the container path rather than the host path', () => {
    renderTab()

    expect(screen.getByText(/container sees it, not the host path/i)).toBeTruthy()
  })

  // Mirrors asset_folders=True in poster_renamer.py and artwork_rename.py.
  it('shows the per-item folder layout when asset folders are on', () => {
    renderTab(config({ asset_folders: true }))

    expect(screen.getByText('/kometa/config/assets/Title (2023)/poster.jpg')).toBeTruthy()
    expect(screen.getByText('/kometa/config/assets/Title (2023)/logo.png')).toBeTruthy()
  })

  // Mirrors the flat branch: "<folder><ext>" for posters, "<folder> - logo.png" for artwork.
  it('shows the flat layout when asset folders are off', () => {
    renderTab(config({ asset_folders: false }))

    expect(screen.getByText('/kometa/config/assets/Title (2023).jpg')).toBeTruthy()
    expect(screen.getByText('/kometa/config/assets/Title (2023) - logo.png')).toBeTruthy()
  })

  it('tracks the typed destination', () => {
    renderTab(config({ destination: '/mnt/user/appdata/kometa/assets' }))

    expect(screen.getByText('/mnt/user/appdata/kometa/assets/Title (2023)/poster.jpg')).toBeTruthy()
  })

  // Blank previews the default because saving blank is what actually gets stored.
  it('previews the default path before a destination is set', () => {
    renderTab(config({ destination: '' }))

    expect(screen.getByText('/config/posters/assets/Title (2023)/poster.jpg')).toBeTruthy()
  })

  it('documents the blank-to-default fallback', () => {
    renderTab()

    const help = screen.getByText(/single folder PosterFlow renames into/i)
    expect(help.textContent).toMatch(/Leave blank to use the default/i)
    expect(help.textContent).toContain('/config/posters/assets')
  })

  it('does not double the separator on a trailing slash', () => {
    renderTab(config({ destination: '/kometa/config/assets/' }))

    expect(screen.getByText('/kometa/config/assets/Title (2023)/poster.jpg')).toBeTruthy()
  })

  it('still edits the destination', async () => {
    const user = userEvent.setup()
    const { onConfigChange } = renderTab(config({ destination: '' }))

    await user.type(screen.getByPlaceholderText('ex. /kometa/config/assets'), '/x')
    expect(onConfigChange).toHaveBeenCalled()
  })
})
