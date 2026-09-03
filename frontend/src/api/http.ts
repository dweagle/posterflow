import axios, { type AxiosRequestConfig } from 'axios'

export const AUTH_TOKEN_STORAGE_KEY = 'posterflow.auth.token'

export const getApiUrl = () => {
  if (import.meta.env.VITE_API_URL) {
    return import.meta.env.VITE_API_URL
  }

  return window.location.origin
}

export const API_URL = getApiUrl()

const client = axios.create({
  baseURL: API_URL,
  headers: {
    'Content-Type': 'application/json',
  },
})

// Inject the stored auth token into every request
client.interceptors.request.use((config) => {
  const token = localStorage.getItem(AUTH_TOKEN_STORAGE_KEY)
  if (token) {
    config.headers = config.headers ?? {}
    config.headers['Authorization'] = `Bearer ${token}`
  }
  return config
})

// Fired when the server reports a newer version than this bundle was built as
export const NEW_VERSION_EVENT = 'app:new-version'
let lastVersionDispatch = 0

// "0.15.1" -> [0, 15, 1]; non-numeric parts count as 0 so odd strings never throw
const parseVersion = (v: string): number[] =>
  v.replace(/^v/, '').split('.').slice(0, 3).map((part) => parseInt(part, 10) || 0)

export const isNewerVersion = (candidate: string, current: string): boolean => {
  const a = parseVersion(candidate)
  const b = parseVersion(current)
  for (let i = 0; i < 3; i++) {
    const x = a[i] ?? 0
    const y = b[i] ?? 0
    if (x !== y) return x > y
  }
  return false
}

// On 401 responses, signal the app to show the lock screen. On every response,
// signal the update banner when the server's X-App-Version is newer than the baked-in
// bundle version (throttled — one event per 30s is plenty). Newer, not merely different:
// the browser replays cached API responses still stamped with the pre-update version.
client.interceptors.response.use(
  (response) => {
    const serverVersion = response.headers?.['x-app-version']
    if (
      typeof serverVersion === 'string' && serverVersion &&
      isNewerVersion(serverVersion, __APP_VERSION__) &&
      Date.now() - lastVersionDispatch > 30_000
    ) {
      lastVersionDispatch = Date.now()
      window.dispatchEvent(new CustomEvent(NEW_VERSION_EVENT, { detail: serverVersion }))
    }
    return response
  },
  (error) => {
    if (error.response?.status === 401) {
      window.dispatchEvent(new CustomEvent('auth:unauthorized'))
    }
    return Promise.reject(error)
  }
)

/**
 * Call this after the user successfully unlocks the app (or clears the password).
 * Pass null to remove the token from storage and headers.
 */
export const setAuthToken = (token: string | null): void => {
  if (token) {
    localStorage.setItem(AUTH_TOKEN_STORAGE_KEY, token)
  } else {
    localStorage.removeItem(AUTH_TOKEN_STORAGE_KEY)
  }
}

export const getData = async <T>(url: string, config?: AxiosRequestConfig): Promise<T> => {
  const response = await client.get<T>(url, config)
  return response.data
}

export const postData = async <T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> => {
  const response = await client.post<T>(url, data, config)
  return response.data
}

export const putData = async <T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> => {
  const response = await client.put<T>(url, data, config)
  return response.data
}

export const patchData = async <T>(url: string, data?: unknown, config?: AxiosRequestConfig): Promise<T> => {
  const response = await client.patch<T>(url, data, config)
  return response.data
}

export const deleteData = async <T>(url: string, config?: AxiosRequestConfig): Promise<T> => {
  const response = await client.delete<T>(url, config)
  return response.data
}

export default client
