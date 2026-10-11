import { beforeEach, describe, expect, it, vi } from 'vitest'
import { getData } from '../../src/api/http'
import { getSettings } from '../../src/api/settings'

vi.mock('../../src/api/http', () => ({ getData: vi.fn(), postData: vi.fn() }))
const mockedGetData = vi.mocked(getData)

describe('getSettings', () => {
  beforeEach(() => {
    mockedGetData.mockReset()
  })

  it('shares one request between callers in the same task', async () => {
    mockedGetData.mockResolvedValue({ tmdb_api_key: 'k' })
    const results = await Promise.all([getSettings(), getSettings(), getSettings()])
    expect(mockedGetData).toHaveBeenCalledTimes(1)
    expect(results.every((r) => r.tmdb_api_key === 'k')).toBe(true)
  })

  it('fetches fresh in a later task', async () => {
    mockedGetData.mockResolvedValueOnce({ a: '1' }).mockResolvedValueOnce({ a: '2' })
    expect((await getSettings()).a).toBe('1')
    await new Promise((r) => setTimeout(r, 0))
    expect((await getSettings()).a).toBe('2')
    expect(mockedGetData).toHaveBeenCalledTimes(2)
  })

  it('does not pin a failed request on later callers', async () => {
    mockedGetData.mockRejectedValueOnce(new Error('down')).mockResolvedValueOnce({ a: 'ok' })
    await expect(getSettings()).rejects.toThrow('down')
    expect((await getSettings()).a).toBe('ok')
  })
})
