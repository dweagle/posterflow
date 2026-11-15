import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { cleanup, renderHook } from '@testing-library/react'

const mocks = vi.hoisted(() => ({
  storeDiscordIdentity: vi.fn(),
  clearCommunityCache: vi.fn(),
  completeClaim: vi.fn(),
  uploadClaimExports: vi.fn(),
  uploadClaimFiles: vi.fn(),
}))

vi.mock('../../src/api/community', () => mocks)
vi.mock('../../src/api/client', () => ({
  // the server's own reason, as the real helper reads it off a failed request
  getApiErrorMessage: (e: { response?: { data?: { detail?: string } }; message?: string }, fallback: string) =>
    e?.response?.data?.detail || e?.message || fallback,
}))

import { useDiscordAuth } from '../../src/hooks/useDiscordAuth'

const refused = (detail: string) => ({ response: { status: 409, data: { detail } } })
const SETTINGS = { action: 'discord_idarr' as const, drive: 'scope:tok-a' }
const RESULT = { posted: 1, messages: 1, idarr: 1, drive: 'Posters', idarr_job: null, warnings: [], did: 'posted 1 poster' }
const realFetch = globalThis.fetch
let token = ''
let fetchMock: ReturnType<typeof vi.fn>

beforeEach(() => {
  const payload = { discord_user_id: '42', discord_username: 'maker', is_maker: true, exp: Math.floor(Date.now() / 1000) + 3600 }
  token = `${btoa(JSON.stringify(payload))}.sig`
  localStorage.setItem('posterflow_discord_auth', JSON.stringify({ ...payload, token }))
  mocks.storeDiscordIdentity.mockResolvedValue({ ok: true })
  mocks.clearCommunityCache.mockResolvedValue({ ok: true })
  fetchMock = vi.fn(async () => ({ ok: true, json: async () => ({ status: 'in_progress', claimed_by: 'maker', fulfilled_by: null }) }))
  globalThis.fetch = fetchMock as unknown as typeof fetch
})

afterEach(() => {
  cleanup()
  vi.clearAllMocks()
  globalThis.fetch = realFetch
  localStorage.clear()
})

describe('useDiscordAuth: upload and complete run on Posterflow\'s server', () => {
  it('uploads files from this computer and exports by name through the server, as this login, never straight to Discord', async () => {
    mocks.uploadClaimFiles.mockResolvedValue(RESULT)
    mocks.uploadClaimExports.mockResolvedValue(RESULT)
    const { result } = renderHook(() => useDiscordAuth())
    const file = new File(['x'], 'a.jpg', { type: 'image/jpeg' })

    expect(await result.current.uploadForClaim('requests', 'r1', [file], SETTINGS)).toEqual(RESULT)
    expect(mocks.uploadClaimFiles).toHaveBeenCalledWith('requests', 'r1', [file], SETTINGS, token, null)

    const refs = [{ source: 'cl2k', name: 'b.jpg' }]
    await result.current.uploadForClaim('list-items', 'i1', refs, SETTINGS)
    expect(mocks.uploadClaimExports).toHaveBeenCalledWith('list-items', 'i1', refs, SETTINGS, token, null)
    expect(fetchMock).not.toHaveBeenCalled()
    expect('uploadPoster' in result.current).toBe(false)
  })

  it('completes a request and a list item through the server, and still sends other actions to the community service', async () => {
    const done = { status: 'fulfilled', claimed_by: 'maker', fulfilled_by: 'maker' }
    mocks.completeClaim.mockResolvedValue(done)
    const { result } = renderHook(() => useDiscordAuth())

    expect(await result.current.updateRequestStatus('r1', 'complete')).toEqual(done)
    expect(mocks.completeClaim).toHaveBeenCalledWith('requests', 'r1', token)
    await result.current.updateListItem('i1', 'complete')
    expect(mocks.completeClaim).toHaveBeenLastCalledWith('list-items', 'i1', token)
    expect(fetchMock).not.toHaveBeenCalled()

    await result.current.updateRequestStatus('r1', 'claim')
    await result.current.updateListItem('i1', 'release')
    expect(fetchMock).toHaveBeenCalledTimes(2)
    expect(String(fetchMock.mock.calls[0][0])).toMatch(/update-request-status$/)
    expect(JSON.parse(fetchMock.mock.calls[0][1].body)).toEqual({ token, request_id: 'r1', action: 'claim' })
    expect(mocks.completeClaim).toHaveBeenCalledTimes(2)
  })

  it('passes a refusal on in the server\'s own words', async () => {
    const { result } = renderHook(() => useDiscordAuth())
    mocks.uploadClaimExports.mockRejectedValue(refused('The Spy (2019) has no Discord thread.'))
    await expect(result.current.uploadForClaim('requests', 'r1', [{ source: 'cl2k', name: 'a.jpg' }], SETTINGS)).rejects.toThrow('The Spy (2019) has no Discord thread.')
    mocks.completeClaim.mockRejectedValue(refused('Your Discord login has expired. Reconnect Discord.'))
    await expect(result.current.updateRequestStatus('r1', 'complete')).rejects.toThrow('Your Discord login has expired. Reconnect Discord.')
    mocks.uploadClaimFiles.mockRejectedValue(new Error('Network Error'))
    await expect(result.current.uploadForClaim('requests', 'r1', [new File(['x'], 'a.jpg')], SETTINGS)).rejects.toThrow('Network Error')
    expect(mocks.uploadClaimExports).toHaveBeenCalledTimes(1)
  })

  it('sends a large upload ten posters a request, paces the Discord messages, and adds up what the parts did', async () => {
    vi.useFakeTimers()
    try {
      const job = { id: 41, index: 1, sync_after: true }
      mocks.uploadClaimFiles
        .mockResolvedValueOnce({ ...RESULT, posted: 10, idarr: 10 })
        .mockResolvedValueOnce({ ...RESULT, posted: 10, idarr: 10, warnings: ['A warning'] })
        .mockResolvedValueOnce({ ...RESULT, posted: 3, idarr: 3, idarr_job: job, warnings: ['A warning'] })
      const { result } = renderHook(() => useDiscordAuth())
      const files = Array.from({ length: 23 }, (_, i) => new File(['x'], `s${i}.jpg`))
      const onProgress = vi.fn()

      const upload = result.current.uploadForClaim('requests', 'big', files, SETTINGS, onProgress)
      await vi.advanceTimersByTimeAsync(0)
      expect(mocks.uploadClaimFiles).toHaveBeenCalledTimes(1)
      await vi.advanceTimersByTimeAsync(1499)
      expect(mocks.uploadClaimFiles).toHaveBeenCalledTimes(1)             // Discord's channel limit: wait between messages
      await vi.advanceTimersByTimeAsync(1)
      expect(mocks.uploadClaimFiles).toHaveBeenCalledTimes(2)
      await vi.advanceTimersByTimeAsync(1500)

      const calls = mocks.uploadClaimFiles.mock.calls
      expect(calls.map((c) => (c[2] as File[]).length)).toEqual([10, 10, 3])
      expect(calls.map((c) => (c[2] as File[])[0].name)).toEqual(['s0.jpg', 's10.jpg', 's20.jpg'])
      const batch = (calls[0][5] as { batch: string }).batch
      expect(batch).toMatch(/^[0-9a-f]{32}$/)                             // the id the server keeps the parts under
      expect(calls.map((c) => c[5])).toEqual([{ batch, part: 1, of: 3 }, { batch, part: 2, of: 3 }, { batch, part: 3, of: 3 }])
      expect(onProgress.mock.calls.map((c) => c[0])).toEqual([{ message: 1, of: 3 }, { message: 2, of: 3 }, { message: 3, of: 3 }])
      await expect(upload).resolves.toEqual({ ...RESULT, posted: 23, messages: 3, idarr: 23, idarr_job: job, warnings: ['A warning'] })
    } finally {
      vi.useRealTimers()
    }
  })

  it('an upload that fits one request has no parts and reports no progress', async () => {
    mocks.uploadClaimExports.mockResolvedValue(RESULT)
    const { result } = renderHook(() => useDiscordAuth())
    const onProgress = vi.fn()
    await result.current.uploadForClaim('requests', 'small', Array.from({ length: 10 }, (_, i) => ({ source: 'cl2k', name: `s${i}.jpg` })), SETTINGS, onProgress)
    expect(mocks.uploadClaimExports).toHaveBeenCalledTimes(1)
    expect(mocks.uploadClaimExports.mock.calls[0][5]).toBeNull()
    expect(onProgress).not.toHaveBeenCalled()
  })

  it('a stopped upload goes on from the part that failed when the same posters are sent again', async () => {
    vi.useFakeTimers()
    try {
      const refs = Array.from({ length: 25 }, (_, i) => ({ source: 'cl2k', name: `s${i}.jpg` }))
      mocks.uploadClaimExports
        .mockResolvedValueOnce({ ...RESULT, posted: 10, idarr: 10 })
        .mockRejectedValueOnce(refused('Discord post failed at message 2 of 3 (earlier messages were posted): boom'))
      const { result } = renderHook(() => useDiscordAuth())
      const first = result.current.uploadForClaim('requests', 'resume', refs, SETTINGS)
      const failed = expect(first).rejects.toThrow('Discord post failed at message 2 of 3')
      await vi.advanceTimersByTimeAsync(1500)
      await failed
      expect(mocks.uploadClaimExports).toHaveBeenCalledTimes(2)
      const batch = (mocks.uploadClaimExports.mock.calls[0][5] as { batch: string }).batch

      mocks.uploadClaimExports
        .mockResolvedValueOnce({ ...RESULT, posted: 10, idarr: 0, warnings: ['They were already in IDarr (Posters)'] })
        .mockResolvedValueOnce({ ...RESULT, posted: 5, idarr: 5 })
      const again = result.current.uploadForClaim('requests', 'resume', refs, SETTINGS)
      await vi.advanceTimersByTimeAsync(1500)
      const parts = mocks.uploadClaimExports.mock.calls.map((c) => c[5])
      expect(parts.slice(2)).toEqual([{ batch, part: 2, of: 3 }, { batch, part: 3, of: 3 }])   // part 1 is not sent again
      await expect(again).resolves.toMatchObject({ posted: 25, idarr: 15, messages: 3, warnings: ['They were already in IDarr (Posters)'] })

      // other posters for the same request are a new upload, from its first part
      mocks.uploadClaimExports.mockResolvedValue(RESULT)
      const other = result.current.uploadForClaim('requests', 'resume', refs.slice(0, 12), SETTINGS)
      await vi.advanceTimersByTimeAsync(1500)
      await other
      const fresh = mocks.uploadClaimExports.mock.calls[4][5] as { batch: string; part: number; of: number }
      expect(fresh.part).toBe(1)
      expect(fresh.of).toBe(2)
      expect(fresh.batch).not.toBe(batch)
    } finally {
      vi.useRealTimers()
    }
  })

  it('parts that post nothing to Discord (IDarr only) are not paced', async () => {
    vi.useFakeTimers()
    try {
      mocks.uploadClaimFiles.mockResolvedValue({ ...RESULT, posted: 0, messages: 0, idarr: 10 })
      const { result } = renderHook(() => useDiscordAuth())
      const upload = result.current.uploadForClaim('list-items', 'quick', Array.from({ length: 11 }, (_, i) => new File(['x'], `s${i}.jpg`)), { action: 'idarr' as const, drive: null })
      await vi.advanceTimersByTimeAsync(0)
      expect(mocks.uploadClaimFiles).toHaveBeenCalledTimes(2)
      await upload
    } finally {
      vi.useRealTimers()
    }
  })

  it('an upload needs no Discord login in the browser (IDarr only does not use one), a complete does', async () => {
    localStorage.clear()
    mocks.uploadClaimFiles.mockResolvedValue(RESULT)
    const { result } = renderHook(() => useDiscordAuth())
    const settings = { action: 'idarr' as const, drive: null }
    await result.current.uploadForClaim('list-items', 'i1', [new File(['x'], 'a.jpg')], settings)
    expect(mocks.uploadClaimFiles.mock.calls[0].slice(3)).toEqual([settings, null, null])
    await expect(result.current.updateRequestStatus('r1', 'complete')).rejects.toThrow('Not connected to Discord')
    expect(mocks.completeClaim).not.toHaveBeenCalled()
  })
})
