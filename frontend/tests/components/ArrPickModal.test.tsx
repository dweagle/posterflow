import { cleanup, render, screen, waitFor, within } from '@testing-library/react'
import userEvent from '@testing-library/user-event'
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import ArrPickModal from '../../src/components/tools/ArrPickModal'

const mockGetArrOptions = vi.fn()

vi.mock('../../src/api/client', () => ({
  getApiErrorMessage: (_err: unknown, fallback: string) => fallback,
}))

vi.mock('../../src/api/tools', () => ({
  getArrOptions: (...args: unknown[]) => mockGetArrOptions(...args),
}))

const options = {
  instance: 'Radarr 4K',
  tags: [{ name: '4k', count: 1 }, { name: 'kids', count: 12 }, { name: 'upgrade-ignore', count: 3 }, { name: 'upgrade-searched', count: 40 }],
  quality_profiles: [{ name: 'HD - 720p/1080p', count: 30 }, { name: 'Ultra-HD', count: 8 }],
}

const renderModal = (props: Partial<Parameters<typeof ArrPickModal>[0]> = {}) => {
  const onApply = vi.fn()
  const onClose = vi.fn()
  render(<ArrPickModal kind="tags" toolId="radarr_upgrade" instance="Radarr 4K" value="" noun="movie" nouns="movies" onClose={onClose} onApply={onApply} {...props} />)
  return { onApply, onClose }
}

describe('ArrPickModal', () => {
  beforeEach(() => {
    vi.clearAllMocks()
    mockGetArrOptions.mockResolvedValue(options)
  })

  afterEach(() => cleanup())

  it('lists the tags of the instance with their counts and fills the field with the ticked ones', async () => {
    const user = userEvent.setup()
    const { onApply } = renderModal({ value: 'KIDS', exclude: ['upgrade-searched', 'Upgrade-Ignore', ''] })
    const dialog = screen.getByRole('dialog', { name: 'Only tags' })
    expect(within(dialog).getByText(/Loading the tags of Radarr 4K/)).toBeTruthy()
    const list = await within(dialog).findByRole('list', { name: 'Only tags' })
    expect(mockGetArrOptions).toHaveBeenCalledWith('radarr_upgrade', 'Radarr 4K')
    // The tool's own search and ignore tags are no filter to pick. What the field names is ticked, whatever its capitals.
    expect(within(list).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['4k 1 movie', 'kids 12 movies'])
    expect((within(list).getByRole('checkbox', { name: /kids/ }) as HTMLInputElement).checked).toBe(true)
    expect(within(dialog).getByText('1 picked')).toBeTruthy()
    await user.click(within(list).getByRole('checkbox', { name: /4k/ }))
    expect(within(dialog).getByText('2 picked')).toBeTruthy()
    await user.click(within(dialog).getByRole('button', { name: 'Apply' }))
    expect(onApply).toHaveBeenCalledWith('4k, kids')  // in the arr's own spelling and order
  })

  it('lists quality profiles, keeps a name the arr no longer has until it is unticked, and can clear the filter', async () => {
    const user = userEvent.setup()
    const { onApply, onClose } = renderModal({ kind: 'profiles', value: 'Old Profile, ultra-hd' })
    const dialog = screen.getByRole('dialog', { name: 'Quality profiles' })
    const list = await within(dialog).findByRole('list', { name: 'Quality profiles' })
    expect(within(list).getAllByRole('listitem').map((li) => li.textContent)).toEqual(['Old Profile not in Radarr 4K', 'HD - 720p/1080p 30 movies', 'Ultra-HD 8 movies'])
    expect(within(dialog).getByText('2 picked')).toBeTruthy()
    await user.click(within(dialog).getByRole('button', { name: 'Apply' }))
    expect(onApply).toHaveBeenLastCalledWith('Old Profile, Ultra-HD')
    await user.click(within(list).getByRole('checkbox', { name: /Old Profile/ }))
    await user.click(within(list).getByRole('checkbox', { name: /Ultra-HD/ }))
    await user.click(within(dialog).getByRole('button', { name: 'Apply' }))
    expect(onApply).toHaveBeenLastCalledWith('')  // none ticked: any profile
    await user.click(within(dialog).getByRole('button', { name: 'Cancel' }))
    expect(onClose).toHaveBeenCalled()
  })

  it('says so when the instance has nothing to pick from, or cannot be read', async () => {
    mockGetArrOptions.mockResolvedValueOnce({ instance: 'Radarr 4K', tags: [], quality_profiles: [] })
    renderModal()
    expect(await screen.findByText('Radarr 4K has no tags to pick from.')).toBeTruthy()
    cleanup()
    mockGetArrOptions.mockRejectedValueOnce(new Error('down'))
    renderModal({ kind: 'profiles' })
    expect((await screen.findByRole('alert')).textContent).toBe('Could not load the quality profiles of Radarr 4K')
    await waitFor(() => expect((screen.getByRole('button', { name: 'Apply' }) as HTMLButtonElement).disabled).toBe(true))
  })
})
