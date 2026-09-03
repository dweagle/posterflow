import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import type { AxiosAdapter } from 'axios'
import client, { isNewerVersion, NEW_VERSION_EVENT } from '../../src/api/http'

describe('isNewerVersion', () => {
  it('is true only when the candidate is strictly newer', () => {
    expect(isNewerVersion('0.15.1', '0.15.0')).toBe(true)
    expect(isNewerVersion('0.16.0', '0.15.9')).toBe(true)
    expect(isNewerVersion('1.0.0', '0.99.99')).toBe(true)
    expect(isNewerVersion('0.15.0', '0.15.1')).toBe(false)
    expect(isNewerVersion('0.15.1', '0.15.1')).toBe(false)
    expect(isNewerVersion('0.14.9', '0.15.0')).toBe(false)
  })

  it('tolerates a v prefix, short strings, and junk', () => {
    expect(isNewerVersion('v0.15.1', '0.15.0')).toBe(true)
    expect(isNewerVersion('0.15', '0.15.0')).toBe(false)
    expect(isNewerVersion('0.15.1', 'v0.15.1')).toBe(false)
    expect(isNewerVersion('garbage', '0.15.0')).toBe(false)
    expect(isNewerVersion('', '0.15.0')).toBe(false)
  })
})

describe('X-App-Version response interceptor', () => {
  const bundle = __APP_VERSION__
  const [major] = bundle.split('.').map(Number)
  const newer = `${major + 1}.0.0`
  const older = '0.0.0'

  const originalAdapter = client.defaults.adapter
  let clock = 1_700_000_000_000

  const respondWith = (version: string | null) => {
    const adapter: AxiosAdapter = async (config) => ({
      data: {},
      status: 200,
      statusText: 'OK',
      headers: version === null ? {} : { 'x-app-version': version },
      config,
    })
    client.defaults.adapter = adapter
  }

  const listen = () => {
    const seen: string[] = []
    const handler = (e: Event) => seen.push((e as CustomEvent<string>).detail)
    window.addEventListener(NEW_VERSION_EVENT, handler)
    return { seen, stop: () => window.removeEventListener(NEW_VERSION_EVENT, handler) }
  }

  beforeEach(() => {
    // Step past the 30s dispatch throttle so each test starts fresh
    clock += 60_000
    vi.useFakeTimers()
    vi.setSystemTime(clock)
  })

  afterEach(() => {
    client.defaults.adapter = originalAdapter
    vi.useRealTimers()
  })

  it('sanity: the derived fixtures sit on either side of the bundle version', () => {
    expect(isNewerVersion(newer, bundle)).toBe(true)
    expect(isNewerVersion(older, bundle)).toBe(false)
  })

  it('stays quiet when a cached response replays the pre-update version', async () => {
    respondWith(older)
    const { seen, stop } = listen()
    await client.get('/api/anything')
    stop()
    expect(seen).toEqual([])
  })

  it('stays quiet when the server matches the bundle', async () => {
    respondWith(bundle)
    const { seen, stop } = listen()
    await client.get('/api/anything')
    stop()
    expect(seen).toEqual([])
  })

  it('stays quiet when the header is absent', async () => {
    respondWith(null)
    const { seen, stop } = listen()
    await client.get('/api/anything')
    stop()
    expect(seen).toEqual([])
  })

  it('fires once with the server version when the server is newer', async () => {
    respondWith(newer)
    const { seen, stop } = listen()
    await client.get('/api/anything')
    await client.get('/api/anything')
    stop()
    expect(seen).toEqual([newer])
  })
})
