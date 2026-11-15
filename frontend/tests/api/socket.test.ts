import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'
import { AUTH_TOKEN_STORAGE_KEY } from '../../src/api/http'
import { openSocket, WS_UNAUTHORIZED } from '../../src/api/socket'

// A socket the test opens and closes by hand, recording the order things happen in.
class FakeSocket extends EventTarget {
  static last: FakeSocket
  onopen: (() => void) | null = null
  log: string[] = []

  constructor(public url: string) {
    super()
    FakeSocket.last = this
  }

  send(data: string) {
    this.log.push(`send ${data}`)
  }

  open() {
    this.dispatchEvent(new Event('open'))
    this.onopen?.()
  }

  closeWith(code: number) {
    this.dispatchEvent(Object.assign(new Event('close'), { code }))
  }
}

describe('openSocket', () => {
  let lockEvents = 0
  const onLock = () => { lockEvents += 1 }

  beforeEach(() => {
    lockEvents = 0
    vi.stubGlobal('WebSocket', FakeSocket)
    window.addEventListener('auth:unauthorized', onLock)
  })

  afterEach(() => {
    window.removeEventListener('auth:unauthorized', onLock)
    localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY)
    vi.unstubAllGlobals()
  })

  it('sends the saved password first, before the caller sees the socket open', () => {
    localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, 'pw')
    const ws = openSocket('ws://host/api/logs/ws')
    const fake = FakeSocket.last
    ws.onopen = () => { fake.log.push('caller onopen') }

    fake.open()

    expect(fake.url).toBe('ws://host/api/logs/ws')
    expect(fake.log).toEqual(['send {"type":"auth","token":"pw"}', 'caller onopen'])
  })

  it('sends nothing when no password is saved', () => {
    openSocket('ws://host/api/jobs/ws')
    FakeSocket.last.open()
    expect(FakeSocket.last.log).toEqual([])
  })

  it('asks for the lock screen only when the server refuses the password', () => {
    openSocket('ws://host/api/jobs/ws')
    FakeSocket.last.closeWith(1006)
    FakeSocket.last.closeWith(1000)
    expect(lockEvents).toBe(0)

    FakeSocket.last.closeWith(WS_UNAUTHORIZED)
    expect(lockEvents).toBe(1)
  })
})
