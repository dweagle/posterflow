import { AUTH_TOKEN_STORAGE_KEY } from './http'

// Close code the server uses for a socket that failed the password check.
export const WS_UNAUTHORIZED = 4401

// Open a websocket. On a password-protected instance the server waits for the saved password
// as the first message, so it goes out before any handler the caller attaches.
export const openSocket = (url: string): WebSocket => {
  const ws = new WebSocket(url)
  ws.addEventListener('open', () => {
    const token = localStorage.getItem(AUTH_TOKEN_STORAGE_KEY)
    if (token) ws.send(JSON.stringify({ type: 'auth', token }))
  })
  ws.addEventListener('close', (event) => {
    if (event.code === WS_UNAUTHORIZED) window.dispatchEvent(new CustomEvent('auth:unauthorized'))
  })
  return ws
}
